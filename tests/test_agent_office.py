"""Tests for the agent-office plugin: event normalization, the office reducer,
the append-only ledger, and the plugin manifests."""

import json
import os
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugins", "agent-office")
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))

import office_paths  # noqa: E402
import office_recorder  # noqa: E402
import office_server  # noqa: E402
import office_state  # noqa: E402


def hook(event, session="s1", **fields):
    payload = {"hook_event_name": event, "session_id": session, "cwd": "/repo"}
    payload.update(fields)
    return payload


def tool_event(name, tool_input, event="PreToolUse", **fields):
    return hook(event, tool_name=name, tool_input=tool_input, **fields)


class RedactionTests(unittest.TestCase):
    def test_masks_assigned_secrets(self):
        masked = office_state.redact("curl -H 'Authorization: Bearer abc123def456' https://x.test")
        self.assertNotIn("abc123def456", masked)
        self.assertIn("[redacted]", masked)

    def test_masks_token_shaped_blobs(self):
        masked = office_state.redact("deploy with ghp_0123456789abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("ghp_0123456789abcdefghijklmnopqrstuvwxyz", masked)

    def test_leaves_ordinary_text_alone(self):
        self.assertEqual(office_state.redact("run the unit tests"), "run the unit tests")

    def test_bash_target_is_redacted(self):
        record = office_state.normalize(tool_event("Bash", {"command": "export API_KEY=supersecretvalue && ./go"}))
        self.assertNotIn("supersecretvalue", json.dumps(record))


class NormalizeTests(unittest.TestCase):
    def test_reading_a_file_sends_a_clerk_to_the_records_room(self):
        record = office_state.normalize(tool_event("Read", {"file_path": "/repo/src/main.py"}))
        self.assertEqual(record["station"], "records")
        self.assertEqual(record["target"], "main.py")
        self.assertIn("main.py", record["activity"])

    def test_each_tool_family_has_its_own_station(self):
        cases = {
            "Bash": ({"command": "pytest -q"}, "server_room"),
            "Write": ({"file_path": "a/b.txt"}, "drafting"),
            "Grep": ({"pattern": "TODO"}, "records"),
            "WebFetch": ({"url": "https://example.com/a/b"}, "mailroom"),
            "Task": ({"description": "audit"}, "war_room"),
            "AskUserQuestion": ({"description": "which port"}, "reception"),
        }
        for tool, (payload, station) in cases.items():
            with self.subTest(tool=tool):
                record = office_state.normalize(tool_event(tool, payload))
                self.assertEqual(record["station"], station)

    def test_web_fetch_keeps_only_the_host(self):
        record = office_state.normalize(tool_event("WebFetch", {"url": "https://example.com/very/long/path?q=1"}))
        self.assertEqual(record["target"], "example.com")

    def test_mcp_tools_go_to_the_mail_room(self):
        record = office_state.normalize(tool_event("mcp__notion__search", {"query": "warehouse"}))
        self.assertEqual(record["station"], "mailroom")
        self.assertIn("notion", record["activity"])

    def test_unknown_tools_still_produce_a_usable_record(self):
        record = office_state.normalize(tool_event("SomeFutureTool", {"thing": "value"}))
        self.assertEqual(record["station"], "desk")
        self.assertTrue(record["activity"])

    def test_long_prompts_are_clipped(self):
        record = office_state.normalize(hook("UserPromptSubmit", prompt="x" * 5000))
        self.assertLessEqual(len(record["detail"]), 181)

    def test_subagent_identity_is_carried(self):
        record = office_state.normalize(hook("SubagentStart", agent_id="a1", agent_type="Explore"))
        self.assertEqual(record["agent_id"], "a1")
        self.assertEqual(record["agent_type"], "Explore")

    def test_garbage_payload_does_not_raise(self):
        for payload in (None, [], "nope", {}):
            record = office_state.normalize(payload)
            self.assertIn("event", record)
            self.assertIn("ts", record)


class OfficeReducerTests(unittest.TestCase):
    def setUp(self):
        self.office = office_state.Office()
        self.clock = 1000.0

    def feed(self, payload, step=1.0):
        self.clock += step
        self.office.apply(office_state.normalize(payload, now=self.clock))
        return self.office

    def worker(self, name):
        for item in self.office.snapshot(now=self.clock)["workers"]:
            if item["name"] == name:
                return item
        return None

    def test_session_start_opens_the_office_with_a_lead(self):
        self.feed(hook("SessionStart", session_start_type="startup"))
        lead = self.worker("Claude")
        self.assertIsNotNone(lead)
        self.assertTrue(lead["lead"])
        self.assertEqual(lead["station"], "desk")

    def test_a_tool_call_walks_the_clerk_over_and_back(self):
        self.feed(hook("SessionStart"))
        self.feed(tool_event("Read", {"file_path": "src/app.py"}))
        self.assertEqual(self.worker("Claude")["station"], "records")
        self.assertEqual(self.worker("Claude")["status"], "working")
        self.feed(tool_event("Read", {"file_path": "src/app.py"}, event="PostToolUse"))
        self.assertEqual(self.worker("Claude")["station"], "desk")
        self.assertEqual(self.worker("Claude")["status"], "idle")
        self.assertEqual(self.worker("Claude")["tasks_done"], 1)

    def test_a_failed_tool_call_blocks_the_clerk_and_counts_the_snag(self):
        self.feed(hook("SessionStart"))
        self.feed(tool_event("Bash", {"command": "pytest"}))
        self.feed(tool_event("Bash", {"command": "pytest"}, event="PostToolUseFailure",
                             tool_response={"text": "2 failed"}))
        worker = self.worker("Claude")
        self.assertEqual(worker["status"], "blocked")
        self.assertEqual(worker["errors"], 1)
        self.assertEqual(self.office.snapshot(now=self.clock)["stats"]["errors"], 1)

    def test_a_subagent_is_hired_given_a_desk_and_clocks_out(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Explore"))
        state = self.office.snapshot(now=self.clock)
        hire = [w for w in state["workers"] if not w["lead"]][0]
        self.assertEqual(hire["title"], "Researcher")
        self.assertEqual(hire["status"], "arriving")
        self.assertNotEqual(hire["desk"], state["workers"][0]["desk"])
        self.assertEqual(state["stats"]["hires"], 1)

        self.feed(hook("SubagentStop", agent_id="a1", agent_type="Explore"))
        self.assertEqual(self.worker(hire["name"])["status"], "gone")

    def test_a_freed_desk_is_reused_by_the_next_hire(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Explore"))
        first = [w for w in self.office.snapshot(now=self.clock)["workers"] if not w["lead"]][0]
        self.feed(hook("SubagentStop", agent_id="a1", agent_type="Explore"))
        self.feed(hook("SubagentStart", agent_id="a2", agent_type="Plan"))
        second = [w for w in self.office.snapshot(now=self.clock)["workers"]
                  if not w["lead"] and w["status"] != "gone"][0]
        self.assertEqual(first["desk"], second["desk"])

    def test_departed_workers_leave_the_snapshot_after_the_grace_period(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Explore"))
        self.feed(hook("SubagentStop", agent_id="a1", agent_type="Explore"))
        later = self.clock + office_state.DEPARTURE_GRACE_SECONDS + 1
        names = [w["name"] for w in self.office.snapshot(now=later)["workers"]]
        self.assertEqual(names, ["Claude"])

    def test_a_permission_prompt_sends_the_lead_to_reception(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("Notification", notification_type="permission_prompt"))
        worker = self.worker("Claude")
        self.assertEqual(worker["station"], "reception")
        self.assertEqual(worker["status"], "waiting")

    def test_prompts_become_tickets_that_close_when_the_turn_ends(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("UserPromptSubmit", prompt="ship the office view"))
        tickets = self.office.snapshot(now=self.clock)["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["status"], "open")
        self.assertIn("ship the office", tickets[0]["text"])
        self.feed(hook("Stop", last_assistant_message="done"))
        self.assertEqual(self.office.snapshot(now=self.clock)["tickets"][0]["status"], "done")

    def test_session_end_empties_the_floor(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Plan"))
        self.feed(hook("SessionEnd"))
        state = self.office.snapshot(now=self.clock)
        self.assertTrue(all(w["status"] == "gone" for w in state["workers"]))
        self.assertEqual(state["stats"]["headcount"], 0)

    def test_two_sessions_share_the_floor_without_colliding(self):
        self.feed(hook("SessionStart", session="s1"))
        self.feed(hook("SessionStart", session="s2"))
        desks = {w["desk"] for w in self.office.snapshot(now=self.clock)["workers"]}
        self.assertEqual(len(desks), 2)
        self.assertEqual(self.office.snapshot(now=self.clock)["stats"]["sessions"], 2)

    def test_stations_report_their_occupants(self):
        self.feed(hook("SessionStart"))
        self.feed(tool_event("Grep", {"pattern": "TODO"}))
        stations = self.office.snapshot(now=self.clock)["stations"]
        self.assertEqual(stations["records"]["occupants"], ["Claude"])
        self.assertEqual(stations["records"]["uses"], 1)

    def test_snapshot_is_json_serializable(self):
        self.feed(hook("SessionStart"))
        self.feed(tool_event("Write", {"file_path": "x.py"}))
        json.dumps(self.office.snapshot(now=self.clock))

    def test_unknown_events_are_logged_not_dropped(self):
        self.feed(hook("SessionStart"))
        before = self.office.snapshot(now=self.clock)["stats"]["events"]
        self.feed(hook("SomeBrandNewHook"))
        self.assertEqual(self.office.snapshot(now=self.clock)["stats"]["events"], before + 1)

    def test_build_sorts_events_by_timestamp(self):
        events = [
            office_state.normalize(tool_event("Read", {"file_path": "b.py"}), now=20),
            office_state.normalize(hook("SessionStart"), now=10),
        ]
        state = office_state.build(events, now=25)
        self.assertEqual(state["workers"][0]["station"], "records")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous = os.environ.get("AGENT_OFFICE_HOME")
        os.environ["AGENT_OFFICE_HOME"] = self.tmp.name
        self.addCleanup(self.restore)
        office_paths.ensure_dirs()

    def restore(self):
        if self.previous is None:
            os.environ.pop("AGENT_OFFICE_HOME", None)
        else:
            os.environ["AGENT_OFFICE_HOME"] = self.previous

    def test_recorder_appends_one_line_per_event(self):
        for payload in (hook("SessionStart"), tool_event("Read", {"file_path": "a.py"})):
            office_recorder.append(office_state.normalize(payload))
        with open(office_paths.ledger_path()) as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual([item["event"] for item in lines], ["SessionStart", "PreToolUse"])

    def test_oversized_records_are_trimmed_to_valid_json(self):
        record = office_state.normalize(hook("UserPromptSubmit", prompt="x"))
        record["detail"] = "y" * 20000
        office_recorder.append(record)
        with open(office_paths.ledger_path()) as fh:
            parsed = json.loads(fh.readline())
        self.assertEqual(parsed["event"], "UserPromptSubmit")

    def test_recorder_reads_a_hook_payload_from_a_stream(self):
        import io

        payload = json.dumps(hook("SessionStart", session_start_type="resume"))
        self.assertEqual(office_recorder.read_payload(io.StringIO(payload))["session_id"], "s1")
        self.assertEqual(office_recorder.read_payload(io.StringIO("not json")), {})
        self.assertEqual(office_recorder.read_payload(io.StringIO("")), {})

    def test_unrecognisable_stdin_writes_nothing(self):
        import io

        for raw in ("", "not json", "[]", "{}", '{"session_id": "s1"}'):
            self.assertEqual(office_recorder.main(stream=io.StringIO(raw)), 0)
        self.assertFalse(os.path.exists(office_paths.ledger_path()))

    def test_a_real_payload_on_stdin_is_recorded(self):
        import io

        office_recorder.main(stream=io.StringIO(json.dumps(hook("SessionStart"))))
        with open(office_paths.ledger_path()) as fh:
            self.assertEqual(json.loads(fh.readline())["event"], "SessionStart")

    def test_server_ledger_tails_new_events(self):
        office_recorder.append(office_state.normalize(hook("SessionStart")))
        ledger = office_server.Ledger(days=1)
        first = ledger.snapshot()
        self.assertEqual(first["stats"]["headcount"], 1)

        office_recorder.append(office_state.normalize(tool_event("Bash", {"command": "ls"})))
        self.assertEqual(ledger.refresh(), 1)
        second = ledger.snapshot()
        self.assertGreater(second["revision"], first["revision"])
        self.assertEqual(second["workers"][0]["station"], "server_room")

    def test_ledger_ignores_a_half_written_line(self):
        office_recorder.append(office_state.normalize(hook("SessionStart")))
        with open(office_paths.ledger_path(), "a") as fh:
            fh.write('{"v":1,"ts":' + str(time.time()))  # no newline yet
        ledger = office_server.Ledger(days=1)
        self.assertEqual(ledger.snapshot()["stats"]["events"], 1)

    def test_ledger_skips_corrupt_lines(self):
        office_recorder.append(office_state.normalize(hook("SessionStart")))
        with open(office_paths.ledger_path(), "a") as fh:
            fh.write("this is not json\n")
        office_recorder.append(office_state.normalize(tool_event("Read", {"file_path": "a.py"})))
        self.assertEqual(office_server.Ledger(days=1).snapshot()["stats"]["events"], 2)

    def test_prune_keeps_only_recent_days(self):
        for day in range(4):
            path = os.path.join(office_paths.events_dir(), "2026-01-0%d.jsonl" % (day + 1))
            with open(path, "w") as fh:
                fh.write("\n")
        office_paths.prune(keep_days=2)
        self.assertEqual(len(os.listdir(office_paths.events_dir())), 2)

    def test_home_prefers_the_plugin_data_directory(self):
        os.environ.pop("AGENT_OFFICE_HOME")
        os.environ["CLAUDE_PLUGIN_DATA"] = self.tmp.name
        self.addCleanup(lambda: os.environ.pop("CLAUDE_PLUGIN_DATA", None))
        try:
            self.assertEqual(office_paths.home(), os.path.join(self.tmp.name, "office"))
        finally:
            os.environ["AGENT_OFFICE_HOME"] = self.tmp.name


class ManifestTests(unittest.TestCase):
    def test_marketplace_points_at_the_plugin(self):
        with open(os.path.join(REPO, ".claude-plugin", "marketplace.json")) as fh:
            marketplace = json.load(fh)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "agent-office")
        self.assertTrue(os.path.isdir(os.path.join(REPO, entry["source"])))
        self.assertIn("name", marketplace["owner"])

    def test_plugin_manifest_is_well_formed(self):
        with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")) as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["name"], "agent-office")
        self.assertTrue(manifest["description"])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_every_hook_runs_the_recorder(self):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json")) as fh:
            config = json.load(fh)
        self.assertIn("SessionStart", config["hooks"])
        self.assertIn("PreToolUse", config["hooks"])
        for event, matchers in config["hooks"].items():
            for matcher in matchers:
                for entry in matcher["hooks"]:
                    self.assertEqual(entry["type"], "command")
                    self.assertIn("office_recorder.py", entry["command"])
                    self.assertTrue(entry["async"], "%s must not block the session" % event)

    def test_the_recorder_is_executable(self):
        self.assertTrue(os.access(os.path.join(PLUGIN, "scripts", "office_recorder.py"), os.X_OK))

    def test_every_station_the_reducer_uses_is_on_the_floor_plan(self):
        for station, _template in office_state.TOOL_STATIONS.values():
            self.assertIn(station, office_state.STATIONS)
        with open(os.path.join(PLUGIN, "web", "office.js")) as fh:
            page = fh.read()
        for station in office_state.STATIONS:
            self.assertIn(station + ":", page, "%s has no place in the drawing" % station)


if __name__ == "__main__":
    unittest.main(verbosity=2)

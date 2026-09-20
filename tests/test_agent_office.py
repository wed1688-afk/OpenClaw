# -*- coding: utf-8 -*-
"""Tests for the agent-office plugin: event normalization, the office reducer,
the language tables, the append-only ledger, and the plugin manifests."""

import json
import os
import re
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
import office_text  # noqa: E402


def hook(event, session="s1", **fields):
    payload = {"hook_event_name": event, "session_id": session, "cwd": "/repo"}
    payload.update(fields)
    return payload


def tool_event(name, tool_input, event="PreToolUse", **fields):
    return hook(event, tool_name=name, tool_input=tool_input, **fields)


def read_page():
    with open(os.path.join(PLUGIN, "web", "office.js"), encoding="utf-8") as fh:
        return fh.read()


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
        self.assertEqual(record["verb"], "read")
        self.assertEqual(record["target"], "main.py")

    def test_the_ledger_stores_keys_not_sentences(self):
        record = office_state.normalize(tool_event("Grep", {"pattern": "TODO"}))
        self.assertNotIn("activity", record)
        self.assertEqual(record["verb"], "grep")

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
                self.assertEqual(office_state.normalize(tool_event(tool, payload))["station"], station)

    def test_web_fetch_keeps_only_the_host(self):
        record = office_state.normalize(tool_event("WebFetch", {"url": "https://example.com/very/long/path?q=1"}))
        self.assertEqual(record["target"], "example.com")

    def test_mcp_tools_go_to_the_mail_room(self):
        record = office_state.normalize(tool_event("mcp__notion__search", {"query": "warehouse"}))
        self.assertEqual(record["station"], "mailroom")
        self.assertEqual(record["verb"], "mcp")
        self.assertEqual(record["target"], "notion")

    def test_unknown_tools_still_produce_a_usable_record(self):
        record = office_state.normalize(tool_event("SomeFutureTool", {"thing": "value"}))
        self.assertEqual(record["station"], "desk")
        self.assertEqual(record["verb"], "generic")

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


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.pop("AGENT_OFFICE_LANG", None)
        self.addCleanup(self.restore)

    def restore(self):
        if self.previous is None:
            os.environ.pop("AGENT_OFFICE_LANG", None)
        else:
            os.environ["AGENT_OFFICE_LANG"] = self.previous

    def test_traditional_chinese_is_the_default(self):
        self.assertEqual(office_text.normalize_locale(None), "zh-Hant")

    def test_the_environment_can_choose_the_language(self):
        os.environ["AGENT_OFFICE_LANG"] = "en"
        self.assertEqual(office_text.normalize_locale(None), "en")

    def test_common_language_tags_are_understood(self):
        for tag in ("zh", "zh-TW", "zh_tw", "ZH-HANT", "tw"):
            with self.subTest(tag=tag):
                self.assertEqual(office_text.normalize_locale(tag), "zh-Hant")
        self.assertEqual(office_text.normalize_locale("en-GB"), "en")

    def test_an_unknown_language_falls_back_instead_of_failing(self):
        self.assertEqual(office_text.normalize_locale("klingon"), "zh-Hant")

    def test_every_verb_the_reducer_can_emit_is_translated(self):
        verbs = {verb for _station, verb in office_state.TOOL_STATIONS.values()}
        verbs.update({"generic", "mcp"})
        for code in office_text.LOCALES:
            for verb in verbs:
                with self.subTest(locale=code, verb=verb):
                    self.assertIn(verb, office_text.LOCALES[code]["verbs"])

    def test_every_phrase_the_reducer_uses_exists_in_every_language(self):
        with open(os.path.join(PLUGIN, "scripts", "office_state.py"), encoding="utf-8") as fh:
            source = fh.read()
        used = set(re.findall(r'_phrase\(\s*"([a-z]+)"\s*,\s*"([a-z_]+)"', source))
        self.assertGreater(len(used), 10, "the scan should find the reducer's phrases")
        for code, table in office_text.LOCALES.items():
            for section, key in sorted(used):
                with self.subTest(locale=code, phrase="%s.%s" % (section, key)):
                    self.assertIn(key, table.get(section, {}))

    def test_every_room_and_role_is_named_in_every_language(self):
        roles = set(office_state.TOOL_STATIONS) and {"lead", "Explore", "Plan", "general-purpose", "claude"}
        for code, table in office_text.LOCALES.items():
            for room in office_state.STATIONS:
                self.assertIn(room, table["stations"], "%s has no name in %s" % (room, code))
            for role in roles:
                self.assertIn(role, table["roles"], "%s has no title in %s" % (role, code))

    def test_session_kinds_are_translated(self):
        for code, table in office_text.LOCALES.items():
            for kind in ("startup", "resume", "clear", "compact", "fork"):
                self.assertIn(kind, table["kind"], "%s missing in %s" % (kind, code))

    def test_names_are_stable_within_a_language_and_differ_across_them(self):
        self.assertEqual(office_text.worker_name("zh-Hant", "s:a"), office_text.worker_name("zh-Hant", "s:a"))
        self.assertNotEqual(office_text.worker_name("zh-Hant", "s:a"), office_text.worker_name("en", "s:a"))

    def test_an_unknown_role_is_still_given_a_title(self):
        self.assertTrue(office_text.role_title("zh-Hant", "some-new-agent"))
        self.assertEqual(office_text.role_title("en", "some-new-agent"), "Some New Agent")

    def test_chinese_sets_a_space_against_latin_but_not_against_itself(self):
        self.assertEqual(office_text.text("zh-Hant", "verbs", "read", target="main.py"),
                         "從檔案櫃抽出 main.py")
        self.assertEqual(office_text.text("zh-Hant", "verbs", "task", target="彙整發現"),
                         "向新人交代彙整發現")
        self.assertEqual(office_text.text("zh-Hant", "verbs", "bash_output", target="pytest -q"),
                         "查看 pytest -q 的進度")
        self.assertEqual(office_text.text("zh-Hant", "log", "filed", target="工單"), "把工單歸檔")

    def test_the_spacing_rule_leaves_english_alone(self):
        self.assertEqual(office_text.text("en", "verbs", "read", target="main.py"),
                         "pulling main.py from the cabinet")
        self.assertEqual(office_text.text("en", "verbs", "bash_output", target="pytest -q"),
                         "checking on pytest -q")

    def test_no_chinese_template_hand_writes_a_space_around_a_field(self):
        """Spacing is the formatter's job, so the table must not bake it in."""
        table = office_text.LOCALES["zh-Hant"]
        for section in ("verbs", "doing", "log"):
            for key, template in table[section].items():
                with self.subTest(phrase="%s.%s" % (section, key)):
                    self.assertNotRegex(template, r"(\s\{\w+\})|(\{\w+\}\s)")

    def test_missing_keys_fall_back_to_english_rather_than_crashing(self):
        self.assertEqual(office_text.text("zh-Hant", "log", "no_such_key"), "no_such_key")
        self.assertTrue(office_text.text("zh-Hant", "verbs", "read", target="a.py"))


class OfficeReducerTests(unittest.TestCase):
    """The reducer is asserted in English so the expectations read clearly;
    the same folds are checked in Traditional Chinese in WordingTests."""

    locale = "en"

    def setUp(self):
        self.office = office_state.Office()
        self.clock = 1000.0

    def feed(self, payload, step=1.0):
        self.clock += step
        self.office.apply(office_state.normalize(payload, now=self.clock))
        return self.office

    def state(self):
        return self.office.snapshot(now=self.clock, locale=self.locale)

    def worker(self, name):
        for item in self.state()["workers"]:
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
        self.assertIn("app.py", self.worker("Claude")["activity"])
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
        self.assertEqual(self.state()["stats"]["errors"], 1)
        self.assertIn("2 failed", self.state()["log"][-1]["text"])

    def test_a_subagent_is_hired_given_a_desk_and_clocks_out(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Explore"))
        state = self.state()
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
        first = [w for w in self.state()["workers"] if not w["lead"]][0]
        self.feed(hook("SubagentStop", agent_id="a1", agent_type="Explore"))
        self.feed(hook("SubagentStart", agent_id="a2", agent_type="Plan"))
        second = [w for w in self.state()["workers"] if not w["lead"] and w["status"] != "gone"][0]
        self.assertEqual(first["desk"], second["desk"])

    def test_departed_workers_leave_the_snapshot_after_the_grace_period(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Explore"))
        self.feed(hook("SubagentStop", agent_id="a1", agent_type="Explore"))
        later = self.clock + office_state.DEPARTURE_GRACE_SECONDS + 1
        names = [w["name"] for w in self.office.snapshot(now=later, locale=self.locale)["workers"]]
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
        tickets = self.state()["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["status"], "open")
        self.assertIn("ship the office", tickets[0]["text"])
        self.feed(hook("Stop", last_assistant_message="done"))
        self.assertEqual(self.state()["tickets"][0]["status"], "done")

    def test_session_end_empties_the_floor(self):
        self.feed(hook("SessionStart"))
        self.feed(hook("SubagentStart", agent_id="a1", agent_type="Plan"))
        self.feed(hook("SessionEnd"))
        state = self.state()
        self.assertTrue(all(w["status"] == "gone" for w in state["workers"]))
        self.assertEqual(state["stats"]["headcount"], 0)

    def test_two_sessions_share_the_floor_without_colliding(self):
        self.feed(hook("SessionStart", session="s1"))
        self.feed(hook("SessionStart", session="s2"))
        desks = {w["desk"] for w in self.state()["workers"]}
        self.assertEqual(len(desks), 2)
        self.assertEqual(self.state()["stats"]["sessions"], 2)

    def test_stations_report_their_occupants(self):
        self.feed(hook("SessionStart"))
        self.feed(tool_event("Grep", {"pattern": "TODO"}))
        stations = self.state()["stations"]
        self.assertEqual(stations["records"]["occupants"], ["Claude"])
        self.assertEqual(stations["records"]["uses"], 1)

    def test_snapshot_is_json_serializable(self):
        self.feed(hook("SessionStart"))
        self.feed(tool_event("Write", {"file_path": "x.py"}))
        json.dumps(self.state())

    def test_unknown_events_are_logged_not_dropped(self):
        self.feed(hook("SessionStart"))
        before = self.state()["stats"]["events"]
        self.feed(hook("SomeBrandNewHook"))
        self.assertEqual(self.state()["stats"]["events"], before + 1)

    def test_build_sorts_events_by_timestamp(self):
        events = [
            office_state.normalize(tool_event("Read", {"file_path": "b.py"}), now=20),
            office_state.normalize(hook("SessionStart"), now=10),
        ]
        state = office_state.build(events, now=25, locale="en")
        self.assertEqual(state["workers"][0]["station"], "records")


class WordingTests(unittest.TestCase):
    """The same ledger, read back in either language."""

    def setUp(self):
        self.office = office_state.Office()
        events = [
            hook("SessionStart", session_start_type="startup"),
            hook("UserPromptSubmit", prompt="整理倉庫資料"),
            tool_event("Read", {"file_path": "/repo/main.py"}),
            tool_event("Read", {"file_path": "/repo/main.py"}, event="PostToolUse"),
            hook("SubagentStart", agent_id="a1", agent_type="Explore"),
            tool_event("Bash", {"command": "pytest -q"}, agent_id="a1", agent_type="Explore"),
        ]
        for index, payload in enumerate(events):
            self.office.apply(office_state.normalize(payload, now=1000.0 + index))
        self.now = 1010.0

    def snap(self, locale):
        return self.office.snapshot(now=self.now, locale=locale)

    def test_the_default_floor_is_in_traditional_chinese(self):
        state = self.snap(None)
        self.assertEqual(state["locale"], "zh-Hant")
        self.assertEqual(state["stations"]["records"]["label"], "檔案室")
        self.assertEqual(state["stations"]["server_room"]["label"], "機房")
        lead = state["workers"][0]
        self.assertEqual(lead["title"], "主管")

    def test_activities_are_worded_in_the_requested_language(self):
        chinese = self.snap("zh-Hant")
        english = self.snap("en")
        hire_zh = [w for w in chinese["workers"] if not w["lead"]][0]
        hire_en = [w for w in english["workers"] if not w["lead"]][0]
        self.assertIn("pytest -q", hire_zh["activity"])
        self.assertIn("執行", hire_zh["activity"])
        self.assertIn("running", hire_en["activity"])
        self.assertEqual(hire_zh["title"], "研究員")
        self.assertEqual(hire_en["title"], "Researcher")

    def test_history_is_re_read_in_the_new_language_not_left_behind(self):
        chinese = self.snap("zh-Hant")
        english = self.snap("en")
        self.assertEqual(len(chinese["log"]), len(english["log"]))
        self.assertIn("從檔案櫃抽出 main.py", [entry["text"] for entry in chinese["log"]])
        self.assertIn("pulling main.py from the cabinet", [entry["text"] for entry in english["log"]])

    def test_the_users_own_words_are_never_translated(self):
        for locale in ("zh-Hant", "en"):
            self.assertEqual(self.snap(locale)["tickets"][0]["text"], "整理倉庫資料")

    def test_the_snapshot_advertises_the_languages_on_offer(self):
        codes = [item["code"] for item in self.snap(None)["locales"]]
        self.assertIn("zh-Hant", codes)
        self.assertIn("en", codes)

    def test_a_pre_v2_ledger_entry_still_reads(self):
        """Ledgers written before v2 carry a finished sentence and no verb."""
        office = office_state.Office()
        office.apply({"v": 1, "ts": 1.0, "event": "SessionStart", "session": "s", "detail": "startup"})
        office.apply({"v": 1, "ts": 2.0, "event": "PreToolUse", "session": "s", "tool": "Read",
                      "station": "records", "activity": "pulling old.py from the cabinet",
                      "target": "old.py"})
        state = office.snapshot(now=3.0, locale="zh-Hant")
        self.assertEqual(state["workers"][0]["station"], "records")
        self.assertEqual(state["workers"][0]["activity"], "pulling old.py from the cabinet")


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
        with open(office_paths.ledger_path(), encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual([item["event"] for item in lines], ["SessionStart", "PreToolUse"])

    def test_oversized_records_are_trimmed_to_valid_json(self):
        record = office_state.normalize(hook("UserPromptSubmit", prompt="x"))
        record["detail"] = "y" * 20000
        office_recorder.append(record)
        with open(office_paths.ledger_path(), encoding="utf-8") as fh:
            parsed = json.loads(fh.readline())
        self.assertEqual(parsed["event"], "UserPromptSubmit")

    def test_chinese_prompts_survive_the_round_trip(self):
        office_recorder.append(office_state.normalize(hook("UserPromptSubmit", prompt="仁武廠房 300 坪")))
        with open(office_paths.ledger_path(), encoding="utf-8") as fh:
            self.assertIn("仁武廠房", json.loads(fh.readline())["detail"])

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
        with open(office_paths.ledger_path(), encoding="utf-8") as fh:
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

    def test_the_server_can_word_the_same_ledger_either_way(self):
        office_recorder.append(office_state.normalize(hook("SessionStart")))
        office_recorder.append(office_state.normalize(tool_event("Read", {"file_path": "a.py"})))
        ledger = office_server.Ledger(days=1)
        self.assertIn("從檔案櫃", ledger.snapshot(locale="zh-Hant")["workers"][0]["activity"])
        self.assertIn("from the cabinet", ledger.snapshot(locale="en")["workers"][0]["activity"])

    def test_ledger_ignores_a_half_written_line(self):
        office_recorder.append(office_state.normalize(hook("SessionStart")))
        with open(office_paths.ledger_path(), "a", encoding="utf-8") as fh:
            fh.write('{"v":2,"ts":' + str(time.time()))  # no newline yet
        ledger = office_server.Ledger(days=1)
        self.assertEqual(ledger.snapshot()["stats"]["events"], 1)

    def test_ledger_skips_corrupt_lines(self):
        office_recorder.append(office_state.normalize(hook("SessionStart")))
        with open(office_paths.ledger_path(), "a", encoding="utf-8") as fh:
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
        for station, _verb in office_state.TOOL_STATIONS.values():
            self.assertIn(station, office_state.STATIONS)
        page = read_page()
        for station in office_state.STATIONS:
            with self.subTest(station=station):
                self.assertTrue(
                    station + ":" in page or '"%s"' % station in page,
                    "%s has no place in the drawing" % station,
                )

    def test_the_page_carries_both_languages_of_its_own_chrome(self):
        page = read_page()
        for code in office_text.LOCALES:
            self.assertIn(code, page, "the page has no chrome strings for %s" % code)

    def test_the_page_does_not_hardcode_room_names(self):
        """Room names come from the snapshot so they follow the language."""
        page = read_page()
        self.assertNotIn("OFFICE_LABELS", page)
        self.assertIn("room.label", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)

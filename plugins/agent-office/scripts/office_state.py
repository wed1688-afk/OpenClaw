# -*- coding: utf-8 -*-
"""Event model and state reducer for the Agent Office view.

This module is deliberately dependency-free and side-effect-free: it turns raw
Claude Code hook payloads into normalized events (`normalize`), and folds a
stream of those events into an office floor plan (`Office`).  The recorder
writes events, the server replays them; both share the vocabulary defined here.

Wording lives in `office_text`, not here.  An event records *what happened*
(`verb`, `station`, `target`) and the sentence is built when a snapshot is
rendered, so the same ledger reads back in whichever language is asked for.
"""

from __future__ import annotations

import os
import re
import time

import office_text

SCHEMA_VERSION = 2

# How long a departed worker lingers in the snapshot so the UI can walk them out.
DEPARTURE_GRACE_SECONDS = 12.0
LOG_LIMIT = 240
TICKET_LIMIT = 24
MAX_DETAIL = 160

# The rooms on the floor, in the order the legend lists them.  Labels and
# descriptions for each one come from office_text.
STATIONS = (
    "desk",
    "records",
    "drafting",
    "server_room",
    "mailroom",
    "war_room",
    "reception",
    "break_room",
    "door",
)

# tool name -> (station, verb key).  The verb is looked up per language.
TOOL_STATIONS = {
    "Read": ("records", "read"),
    "NotebookRead": ("records", "notebook_read"),
    "Glob": ("records", "glob"),
    "Grep": ("records", "grep"),
    "Edit": ("drafting", "edit"),
    "MultiEdit": ("drafting", "edit"),
    "Write": ("drafting", "write"),
    "NotebookEdit": ("drafting", "notebook_edit"),
    "Bash": ("server_room", "bash"),
    "BashOutput": ("server_room", "bash_output"),
    "KillShell": ("server_room", "kill_shell"),
    "WebFetch": ("mailroom", "web_fetch"),
    "WebSearch": ("mailroom", "web_search"),
    "Task": ("war_room", "task"),
    "Agent": ("war_room", "task"),
    "Workflow": ("war_room", "workflow"),
    "Skill": ("war_room", "skill"),
    "TodoWrite": ("war_room", "todo"),
    "TaskCreate": ("war_room", "task_create"),
    "TaskUpdate": ("war_room", "task_update"),
    "ExitPlanMode": ("war_room", "plan"),
    "AskUserQuestion": ("reception", "ask"),
    "SendUserFile": ("mailroom", "send_file"),
    "Artifact": ("drafting", "artifact"),
}

DEFAULT_STATION = ("desk", "generic")


# --------------------------------------------------------------------------
# Normalizing raw hook payloads
# --------------------------------------------------------------------------

_SECRET_KEYS = r"token|secret|password|passwd|api[-_ ]?key|apikey|auth|bearer|credential"
_SECRET_ASSIGN = re.compile(r"(?i)(" + _SECRET_KEYS + r")([\"'\s]*[:=]\s*|\s+)(\S+)")
_SECRET_BLOB = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{16,}|[A-Fa-f0-9]{40,})\b")


def redact(text):
    """Blank out anything that reads like a credential before it is stored."""
    if not text:
        return ""
    text = _SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
    text = _SECRET_BLOB.sub("[redacted]", text)
    return text


def _clip(text, limit=MAX_DETAIL):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _basename(path):
    path = str(path or "").rstrip("/")
    return os.path.basename(path) or path


def _host(url):
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/\s?#]+)", str(url or ""))
    return match.group(1) if match else _clip(url, 48)


def describe_tool(tool_name, tool_input):
    """Map a tool call onto (station, verb key, short target)."""
    tool_name = tool_name or "?"
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    target = ""
    if "file_path" in tool_input:
        target = _basename(tool_input.get("file_path"))
    elif "notebook_path" in tool_input:
        target = _basename(tool_input.get("notebook_path"))
    elif "pattern" in tool_input:
        target = _clip(tool_input.get("pattern"), 48)
    elif "command" in tool_input:
        target = _clip(redact(str(tool_input.get("command"))), 64)
    elif "url" in tool_input:
        target = _host(tool_input.get("url"))
    elif "query" in tool_input:
        target = _clip(tool_input.get("query"), 48)
    elif "description" in tool_input:
        target = _clip(tool_input.get("description"), 48)
    elif "skill" in tool_input:
        target = _clip(tool_input.get("skill"), 48)
    elif "prompt" in tool_input:
        target = _clip(tool_input.get("prompt"), 48)
    else:
        for value in tool_input.values():
            if isinstance(value, str) and value.strip():
                target = _clip(redact(value), 48)
                break

    station, verb = TOOL_STATIONS.get(tool_name, (None, None))
    if station is None:
        if tool_name.startswith("mcp__"):
            server = tool_name.split("__")[1] if "__" in tool_name else tool_name
            return "mailroom", "mcp", server.replace("_", " ")
        station, verb = DEFAULT_STATION
    return station, verb, target or tool_name


def normalize(payload, now=None):
    """Turn a raw hook payload into the compact record the ledger stores."""
    payload = payload if isinstance(payload, dict) else {}
    event = payload.get("hook_event_name") or "Unknown"
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}

    record = {
        "v": SCHEMA_VERSION,
        "ts": float(now if now is not None else time.time()),
        "event": event,
        "session": str(payload.get("session_id") or "unknown"),
        "cwd": str(payload.get("cwd") or ""),
    }
    if payload.get("agent_id"):
        record["agent_id"] = str(payload["agent_id"])
    if payload.get("agent_type"):
        record["agent_type"] = str(payload["agent_type"])
    if tool_name:
        record["tool"] = tool_name
    if payload.get("tool_use_id"):
        record["tool_use_id"] = str(payload["tool_use_id"])

    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        station, verb, target = describe_tool(tool_name, tool_input)
        record["station"] = station
        record["verb"] = verb
        record["target"] = target
    elif event in ("UserPromptSubmit", "UserPromptExpansion"):
        record["detail"] = _clip(redact(str(payload.get("prompt") or "")), 180)
        record["source"] = str(payload.get("source") or "direct")
    elif event == "SessionStart":
        record["detail"] = str(payload.get("session_start_type") or "startup")
    elif event == "Notification":
        record["detail"] = str(payload.get("notification_type") or "notification")
    elif event == "StopFailure":
        record["detail"] = _clip(
            "{}: {}".format(payload.get("error_type") or "error", payload.get("error_message") or "")
        )
    elif event in ("Stop", "SubagentStop"):
        record["detail"] = _clip(redact(str(payload.get("last_assistant_message") or "")), 180)

    if event == "PostToolUseFailure":
        response = payload.get("tool_response")
        if isinstance(response, dict):
            record["detail"] = _clip(redact(str(response.get("text") or response.get("error") or "")), 180)
        elif response:
            record["detail"] = _clip(redact(str(response)), 180)

    return record


# --------------------------------------------------------------------------
# The reducer
# --------------------------------------------------------------------------


def _phrase(section, key, **args):
    """A phrase to be worded later: (section, key, args)."""
    return {"s": section, "k": key, "a": args}


class Office:
    """Folds a stream of normalized events into a floor plan snapshot."""

    def __init__(self):
        self.workers = {}
        self.sessions = {}
        self.tickets = []
        self.log = []
        self.desks = {}  # desk index -> worker key
        self.stats = {
            "events": 0,
            "tool_calls": 0,
            "errors": 0,
            "prompts": 0,
            "hires": 0,
            "sessions": 0,
            "by_station": {},
            "by_tool": {},
        }
        self.first_ts = None
        self.last_ts = None
        self._ticket_seq = 0
        self._log_seq = 0

    # -- helpers ---------------------------------------------------------

    def _take_desk(self, key):
        for desk in range(0, 64):
            if desk not in self.desks:
                self.desks[desk] = key
                return desk
        return len(self.desks)

    def _free_desk(self, key):
        for desk, owner in list(self.desks.items()):
            if owner == key:
                del self.desks[desk]

    def _worker_key(self, ev):
        agent_id = ev.get("agent_id")
        if agent_id:
            return "{}:{}".format(ev.get("session"), agent_id)
        return str(ev.get("session"))

    def _worker(self, ev, role=None, create=True):
        key = self._worker_key(ev)
        worker = self.workers.get(key)
        if worker is None and create:
            is_lead = ":" not in key
            role = role or ev.get("agent_type") or ("lead" if is_lead else "claude")
            worker = {
                "key": key,
                "session": ev.get("session"),
                "agent_id": ev.get("agent_id", ""),
                "role": "lead" if is_lead else role,
                "lead": is_lead,
                "desk": self._take_desk(key),
                "station": "desk",
                "status": "idle",
                "doing": _phrase("doing", "settling_in"),
                "tool": "",
                "target": "",
                "since": ev.get("ts"),
                "arrived": ev.get("ts"),
                "left": None,
                "tasks_done": 0,
                "errors": 0,
            }
            self.workers[key] = worker
        return worker

    def _note(self, ev, worker, phrase, kind="info"):
        self._log_seq += 1
        self.log.append(
            {
                "id": self._log_seq,
                "ts": ev.get("ts"),
                "session": ev.get("session"),
                "worker_key": worker.get("key") if worker else None,
                "station": (worker or {}).get("station", ""),
                "phrase": phrase,
                "kind": kind,
            }
        )
        if len(self.log) > LOG_LIMIT:
            del self.log[: len(self.log) - LOG_LIMIT]

    def _bump(self, bucket, key):
        if not key:
            return
        counts = self.stats[bucket]
        counts[key] = counts.get(key, 0) + 1

    def _set_busy(self, worker, ev):
        worker["station"] = ev.get("station") or "desk"
        worker["status"] = "working"
        worker["tool"] = ev.get("tool", "")
        worker["target"] = ev.get("target", "")
        worker["since"] = ev.get("ts")
        worker["tool_use_id"] = ev.get("tool_use_id", "")
        if ev.get("verb"):
            worker["doing"] = _phrase("verbs", ev["verb"], target=ev.get("target") or ev.get("tool") or "")
        else:
            # Ledgers written before v2 carry a finished sentence, not a verb.
            worker["doing"] = {"text": ev.get("activity") or ev.get("tool") or ""}

    def _park(self, worker, ev, phrase, status="idle"):
        worker["station"] = "desk"
        worker["status"] = status
        worker["doing"] = phrase
        worker["tool"] = ""
        worker["target"] = ""
        worker["since"] = ev.get("ts")

    # -- event handling --------------------------------------------------

    def apply(self, ev):
        if not isinstance(ev, dict) or not ev.get("event"):
            return
        ts = float(ev.get("ts") or time.time())
        ev = dict(ev, ts=ts)
        self.stats["events"] += 1
        self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)

        handler = getattr(self, "_on_" + ev["event"], None)
        if handler is not None:
            handler(ev)
        else:
            worker = self._worker(ev)
            self._note(ev, worker, _phrase("log", "plain", text=ev["event"]), kind="info")

    def _on_SessionStart(self, ev):
        if ev["session"] not in self.sessions:
            self.stats["sessions"] += 1
        kind = ev.get("detail", "startup")
        self.sessions[ev["session"]] = {
            "id": ev["session"],
            "cwd": ev.get("cwd", ""),
            "opened": ev["ts"],
            "closed": None,
            "kind": kind,
            "turns": 0,
        }
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, _phrase("doing", "opening_office"))
        worker["left"] = None
        self._note(ev, worker, _phrase("log", "session_open", kind_key=kind), kind="session")

    def _on_SessionEnd(self, ev):
        session = self.sessions.get(ev["session"])
        if session:
            session["closed"] = ev["ts"]
        for key, worker in self.workers.items():
            if worker["session"] == ev["session"] and worker["status"] != "gone":
                worker["status"] = "gone"
                worker["station"] = "door"
                worker["doing"] = _phrase("doing", "heading_home")
                worker["left"] = ev["ts"]
                self._free_desk(key)
        self._note(ev, None, _phrase("log", "session_close"), kind="session")

    def _on_UserPromptSubmit(self, ev):
        self._ticket_seq += 1
        self.stats["prompts"] += 1
        session = self.sessions.setdefault(
            ev["session"],
            {"id": ev["session"], "cwd": ev.get("cwd", ""), "opened": ev["ts"],
             "closed": None, "kind": "startup", "turns": 0},
        )
        session["turns"] += 1
        self.tickets.append(
            {
                "id": self._ticket_seq,
                "ts": ev["ts"],
                "session": ev["session"],
                "text": ev.get("detail", ""),
                "source": ev.get("source", "direct"),
                "status": "open",
            }
        )
        if len(self.tickets) > TICKET_LIMIT:
            del self.tickets[: len(self.tickets) - TICKET_LIMIT]
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, _phrase("doing", "reading_order"), status="briefed")
        self._note(ev, worker, _phrase("log", "ticket"), kind="ticket")

    def _on_PreToolUse(self, ev):
        worker = self._worker(ev)
        self._set_busy(worker, ev)
        self.stats["tool_calls"] += 1
        self._bump("by_station", worker["station"])
        self._bump("by_tool", ev.get("tool"))
        self._note(ev, worker, dict(worker["doing"]), kind="work")

    def _on_PostToolUse(self, ev):
        worker = self._worker(ev)
        worker["tasks_done"] += 1
        self._park(worker, ev, _phrase("doing", "back_at_desk"))
        self._note(ev, worker, _phrase("log", "filed", target=ev.get("target") or ev.get("tool") or ""), kind="done")

    def _on_PostToolUseFailure(self, ev):
        worker = self._worker(ev)
        worker["errors"] += 1
        self.stats["errors"] += 1
        self._park(worker, ev, _phrase("doing", "sorting_mess"), status="blocked")
        detail = _clip(ev.get("detail") or ev.get("tool") or "", 90)
        self._note(ev, worker, _phrase("log", "snag", detail=detail), kind="error")

    def _on_SubagentStart(self, ev):
        worker = self._worker(ev, role=ev.get("agent_type") or "claude")
        self.stats["hires"] += 1
        worker["station"] = "door"
        worker["status"] = "arriving"
        worker["doing"] = _phrase("doing", "being_seated")
        worker["since"] = ev["ts"]
        worker["left"] = None
        self._note(ev, worker, _phrase("log", "arrive", role=worker["role"]), kind="arrive")

    def _on_SubagentStop(self, ev):
        worker = self._worker(ev)
        worker["status"] = "gone"
        worker["station"] = "door"
        worker["doing"] = _phrase("doing", "handing_report")
        worker["left"] = ev["ts"]
        self._free_desk(worker["key"])
        self._note(ev, worker, _phrase("log", "depart"), kind="depart")

    def _on_Notification(self, ev):
        worker = self._worker(ev, role="lead")
        kind = ev.get("detail", "notification")
        if kind == "permission_prompt":
            worker["station"] = "reception"
            worker["status"] = "waiting"
            worker["doing"] = _phrase("doing", "waiting_signature")
            worker["since"] = ev["ts"]
            self._note(ev, worker, _phrase("log", "wait"), kind="wait")
        elif kind == "idle_prompt":
            worker["station"] = "break_room"
            worker["status"] = "idle"
            worker["doing"] = _phrase("doing", "waiting_next")
            worker["since"] = ev["ts"]
            self._note(ev, worker, _phrase("log", "break"), kind="idle")
        else:
            self._note(ev, worker, _phrase("log", "plain", text=kind.replace("_", " ")), kind="info")

    def _on_Stop(self, ev):
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, _phrase("doing", "handing_back"))
        for ticket in self.tickets:
            if ticket["session"] == ev["session"] and ticket["status"] == "open":
                ticket["status"] = "done"
                ticket["closed"] = ev["ts"]
        self._note(ev, worker, _phrase("log", "handover"), kind="done")

    def _on_StopFailure(self, ev):
        worker = self._worker(ev, role="lead")
        self.stats["errors"] += 1
        detail = ev.get("detail", "error")
        self._park(worker, ev, _phrase("doing", "line_dead", detail=detail), status="blocked")
        self._note(ev, worker, _phrase("log", "line_dead", detail=detail), kind="error")

    def _on_PreCompact(self, ev):
        worker = self._worker(ev, role="lead")
        worker["station"] = "records"
        worker["status"] = "working"
        worker["doing"] = _phrase("doing", "boxing_papers")
        worker["since"] = ev["ts"]
        self._note(ev, worker, _phrase("log", "compact_start"), kind="info")

    def _on_PostCompact(self, ev):
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, _phrase("doing", "desk_cleared"))
        self._note(ev, worker, _phrase("log", "compact_done"), kind="info")

    # -- wording ---------------------------------------------------------

    def _say(self, locale, phrase):
        """Turn a stored phrase into a sentence in the requested language."""
        if not isinstance(phrase, dict):
            return str(phrase or "")
        if "text" in phrase:  # a pre-v2 ledger entry, already a sentence
            return str(phrase["text"])
        args = dict(phrase.get("a") or {})
        if "kind_key" in args:
            args["kind"] = office_text.text(locale, "kind", args.pop("kind_key"))
        if "role" in args:
            args["title"] = office_text.role_title(locale, args.pop("role"))
        if not args.get("target"):
            args["target"] = office_text.text(locale, "word", "paperwork")
        if not args.get("detail"):
            args["detail"] = office_text.text(locale, "word", "job")
        return office_text.text(locale, phrase.get("s", "log"), phrase.get("k", ""), **args)

    def _name(self, locale, worker):
        if worker is None:
            return office_text.office_name(locale)
        if worker.get("lead"):
            return office_text.lead_name(locale)
        return office_text.worker_name(locale, worker["key"])

    # -- output ----------------------------------------------------------

    def snapshot(self, now=None, locale=None):
        now = float(now if now is not None else time.time())
        locale = office_text.normalize_locale(locale)

        workers = []
        for worker in self.workers.values():
            left = worker.get("left")
            if worker["status"] == "gone" and left and now - left > DEPARTURE_GRACE_SECONDS:
                continue
            item = dict(worker)
            item.pop("doing", None)
            item["name"] = self._name(locale, worker)
            item["title"] = office_text.role_title(locale, worker["role"])
            item["activity"] = self._say(locale, worker.get("doing"))
            item["busy_for"] = max(0.0, now - float(worker.get("since") or now))
            workers.append(item)
        workers.sort(key=lambda w: (not w["lead"], w["desk"]))

        stations = {}
        for name in STATIONS:
            label, blurb = office_text.station(locale, name)
            stations[name] = {
                "label": label,
                "blurb": blurb,
                "occupants": [w["name"] for w in workers if w["station"] == name and w["status"] != "gone"],
                "uses": self.stats["by_station"].get(name, 0),
            }

        log = []
        for entry in self.log[-80:]:
            worker = self.workers.get(entry.get("worker_key")) if entry.get("worker_key") else None
            log.append(
                {
                    "id": entry["id"],
                    "ts": entry["ts"],
                    "session": entry["session"],
                    "worker": self._name(locale, worker),
                    "station": entry["station"],
                    "text": self._say(locale, entry["phrase"]),
                    "kind": entry["kind"],
                }
            )

        busiest = sorted(self.stats["by_tool"].items(), key=lambda kv: -kv[1])[:6]
        active = [w for w in workers if w["status"] != "gone"]
        return {
            "v": SCHEMA_VERSION,
            "locale": locale,
            "locales": office_text.available(),
            "now": now,
            "since": self.first_ts,
            "last_event": self.last_ts,
            "workers": workers,
            "stations": stations,
            "tickets": list(reversed(self.tickets[-TICKET_LIMIT:])),
            "log": log,
            "sessions": list(self.sessions.values()),
            "stats": {
                "events": self.stats["events"],
                "tool_calls": self.stats["tool_calls"],
                "errors": self.stats["errors"],
                "prompts": self.stats["prompts"],
                "hires": self.stats["hires"],
                "sessions": self.stats["sessions"],
                "headcount": len(active),
                "busy": len([w for w in active if w["status"] == "working"]),
                "by_station": dict(self.stats["by_station"]),
                "busiest_tools": [{"tool": t, "count": c} for t, c in busiest],
            },
        }


def build(events, now=None, locale=None):
    """Convenience: fold an iterable of events into a snapshot."""
    office = Office()
    for ev in sorted(events, key=lambda e: float(e.get("ts") or 0)):
        office.apply(ev)
    return office.snapshot(now=now, locale=locale)

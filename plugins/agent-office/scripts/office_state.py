"""Event model and state reducer for the Agent Office view.

This module is deliberately dependency-free and side-effect-free: it turns raw
Claude Code hook payloads into normalized events (`normalize`), and folds a
stream of those events into an office floor plan (`Office`).  The recorder
writes events, the server replays them; both share the vocabulary defined here.
"""

from __future__ import annotations

import hashlib
import os
import re
import time

SCHEMA_VERSION = 1

# How long a departed worker lingers in the snapshot so the UI can walk them out.
DEPARTURE_GRACE_SECONDS = 12.0
# How long a station keeps its "recently used" glow.
LOG_LIMIT = 240
TICKET_LIMIT = 24
MAX_DETAIL = 160

# --------------------------------------------------------------------------
# The floor plan
# --------------------------------------------------------------------------

STATIONS = {
    "desk": {"label": "Bullpen", "blurb": "where the work gets written up"},
    "records": {"label": "Records Room", "blurb": "files, shelves, old paperwork"},
    "drafting": {"label": "Drafting Table", "blurb": "where edits are drawn up"},
    "server_room": {"label": "Server Room", "blurb": "machines, scripts, fans"},
    "mailroom": {"label": "Mail Room", "blurb": "anything from outside the building"},
    "war_room": {"label": "War Room", "blurb": "planning, briefing, the job board"},
    "reception": {"label": "Reception", "blurb": "waiting on a signature"},
    "break_room": {"label": "Break Room", "blurb": "between assignments"},
    "door": {"label": "Front Door", "blurb": "arrivals and departures"},
}

# tool name -> (station, activity template).  `{target}` is filled from the
# tool input; templates must read sensibly when the target is empty.
TOOL_STATIONS = {
    "Read": ("records", "pulling {target} from the cabinet"),
    "NotebookRead": ("records", "leafing through {target}"),
    "Glob": ("records", "scanning the shelves for {target}"),
    "Grep": ("records", "digging through the files for {target}"),
    "Edit": ("drafting", "marking up {target}"),
    "MultiEdit": ("drafting", "marking up {target}"),
    "Write": ("drafting", "drafting {target}"),
    "NotebookEdit": ("drafting", "revising {target}"),
    "Bash": ("server_room", "running {target}"),
    "BashOutput": ("server_room", "checking on {target}"),
    "KillShell": ("server_room", "shutting down {target}"),
    "WebFetch": ("mailroom", "opening mail from {target}"),
    "WebSearch": ("mailroom", "combing the trade papers for {target}"),
    "Task": ("war_room", "briefing a new hire on {target}"),
    "Agent": ("war_room", "briefing a new hire on {target}"),
    "Workflow": ("war_room", "running the {target} playbook"),
    "Skill": ("war_room", "looking up the {target} manual"),
    "TodoWrite": ("war_room", "updating the job board"),
    "TaskCreate": ("war_room", "pinning up {target}"),
    "TaskUpdate": ("war_room", "moving {target} across the board"),
    "ExitPlanMode": ("war_room", "presenting the plan"),
    "AskUserQuestion": ("reception", "asking the boss about {target}"),
    "SendUserFile": ("mailroom", "couriering {target} over"),
    "Artifact": ("drafting", "mounting {target} on the wall"),
}

DEFAULT_STATION = ("desk", "working on {target}")

ROLE_TITLES = {
    "lead": "Desk Lead",
    "Explore": "Researcher",
    "Plan": "Architect",
    "general-purpose": "Generalist",
    "claude": "Associate",
    "code-review": "Reviewer",
    "statusline-setup": "Fitter",
    "fork": "Understudy",
}

NAMES = (
    "Ada", "Bruno", "Cass", "Dara", "Emil", "Fern", "Gita", "Hal", "Ines",
    "Jules", "Kato", "Lena", "Mira", "Nils", "Ozzy", "Pia", "Quinn", "Rue",
    "Sana", "Theo", "Uma", "Vic", "Wren", "Xan", "Yara", "Zeb", "Arlo",
    "Bex", "Cleo", "Dov", "Esme", "Finn", "Gwen", "Hugo", "Iris", "Joss",
)


def worker_name(worker_id: str) -> str:
    digest = hashlib.sha1(worker_id.encode("utf-8", "replace")).digest()
    return NAMES[digest[0] % len(NAMES)]


def role_title(role: str) -> str:
    if role in ROLE_TITLES:
        return ROLE_TITLES[role]
    cleaned = role.replace("_", " ").replace("-", " ").strip()
    return cleaned.title() if cleaned else "Associate"


# --------------------------------------------------------------------------
# Normalizing raw hook payloads
# --------------------------------------------------------------------------

_SECRET_KEYS = r"token|secret|password|passwd|api[-_ ]?key|apikey|auth|bearer|credential"
_SECRET_ASSIGN = re.compile(r"(?i)(" + _SECRET_KEYS + r")([\"'\s]*[:=]\s*|\s+)(\S+)")
_SECRET_BLOB = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{16,}|[A-Fa-f0-9]{40,})\b")


def redact(text: str) -> str:
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


def _basename(path: str) -> str:
    path = str(path or "").rstrip("/")
    return os.path.basename(path) or path


def _host(url: str) -> str:
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/\s?#]+)", str(url or ""))
    return match.group(1) if match else _clip(url, 48)


def describe_tool(tool_name, tool_input):
    """Map a tool call onto (station, activity sentence, short target)."""
    tool_name = tool_name or "something"
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

    station, template = TOOL_STATIONS.get(tool_name, (None, None))
    if station is None:
        if tool_name.startswith("mcp__"):
            server = tool_name.split("__")[1] if "__" in tool_name else tool_name
            station, template = "mailroom", "on the line with " + server.replace("_", " ")
            target = target or tool_name
        else:
            station, template = DEFAULT_STATION

    if "{target}" in template:
        activity = template.format(target=target or tool_name)
    else:
        activity = template
    return station, _clip(activity), target


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
        station, activity, target = describe_tool(tool_name, tool_input)
        record["station"] = station
        record["activity"] = activity
        if target:
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
            "{}: {}".format(
                payload.get("error_type") or "error",
                payload.get("error_message") or "",
            )
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
                "name": "Claude" if is_lead else worker_name(key),
                "role": role,
                "title": "Desk Lead" if is_lead else role_title(role),
                "lead": is_lead,
                "desk": self._take_desk(key),
                "station": "desk",
                "status": "idle",
                "activity": "settling in",
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

    def _note(self, ev, worker, text, kind="info"):
        self._log_seq += 1
        self.log.append(
            {
                "id": self._log_seq,
                "ts": ev.get("ts"),
                "session": ev.get("session"),
                "worker": worker.get("name") if worker else "Office",
                "station": (worker or {}).get("station", ""),
                "text": text,
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
        worker["activity"] = ev.get("activity") or "working"
        worker["tool"] = ev.get("tool", "")
        worker["target"] = ev.get("target", "")
        worker["since"] = ev.get("ts")
        worker["tool_use_id"] = ev.get("tool_use_id", "")

    def _park(self, worker, ev, activity, status="idle"):
        worker["station"] = "desk"
        worker["status"] = status
        worker["activity"] = activity
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
            self._note(ev, worker, ev["event"], kind="info")

    def _on_SessionStart(self, ev):
        session = self.sessions.get(ev["session"])
        if session is None:
            self.stats["sessions"] += 1
        self.sessions[ev["session"]] = {
            "id": ev["session"],
            "cwd": ev.get("cwd", ""),
            "opened": ev["ts"],
            "closed": None,
            "kind": ev.get("detail", "startup"),
            "turns": 0,
        }
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, "opening up the office")
        worker["left"] = None
        self._note(ev, worker, "unlocks the office ({})".format(ev.get("detail", "startup")), kind="session")

    def _on_SessionEnd(self, ev):
        session = self.sessions.get(ev["session"])
        if session:
            session["closed"] = ev["ts"]
        for key, worker in self.workers.items():
            if worker["session"] == ev["session"] and worker["status"] != "gone":
                worker["status"] = "gone"
                worker["station"] = "door"
                worker["activity"] = "heading home"
                worker["left"] = ev["ts"]
                self._free_desk(key)
        self._note(ev, None, "lights out", kind="session")

    def _on_UserPromptSubmit(self, ev):
        self._ticket_seq += 1
        self.stats["prompts"] += 1
        session = self.sessions.setdefault(
            ev["session"],
            {"id": ev["session"], "cwd": ev.get("cwd", ""), "opened": ev["ts"], "closed": None, "kind": "startup", "turns": 0},
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
        self._park(worker, ev, "reading the new work order", status="briefed")
        self._note(ev, worker, "takes a new work order off the counter", kind="ticket")

    def _on_PreToolUse(self, ev):
        worker = self._worker(ev)
        self._set_busy(worker, ev)
        self.stats["tool_calls"] += 1
        self._bump("by_station", worker["station"])
        self._bump("by_tool", ev.get("tool"))
        self._note(ev, worker, worker["activity"], kind="work")

    def _on_PostToolUse(self, ev):
        worker = self._worker(ev)
        worker["tasks_done"] += 1
        self._park(worker, ev, "back at the desk")
        self._note(ev, worker, "files {}".format(ev.get("target") or ev.get("tool") or "the paperwork"), kind="done")

    def _on_PostToolUseFailure(self, ev):
        worker = self._worker(ev)
        worker["errors"] += 1
        self.stats["errors"] += 1
        self._park(worker, ev, "sorting out a mess", status="blocked")
        detail = ev.get("detail") or ev.get("tool") or "the job"
        self._note(ev, worker, "hits a snag: {}".format(_clip(detail, 90)), kind="error")

    def _on_SubagentStart(self, ev):
        worker = self._worker(ev, role=ev.get("agent_type") or "claude")
        self.stats["hires"] += 1
        worker["station"] = "door"
        worker["status"] = "arriving"
        worker["activity"] = "being shown to a desk"
        worker["since"] = ev["ts"]
        worker["left"] = None
        self._note(ev, worker, "arrives as a {}".format(worker["title"].lower()), kind="arrive")

    def _on_SubagentStop(self, ev):
        worker = self._worker(ev)
        worker["status"] = "gone"
        worker["station"] = "door"
        worker["activity"] = "handing in the report"
        worker["left"] = ev["ts"]
        self._free_desk(worker["key"])
        self._note(ev, worker, "hands in the report and clocks out", kind="depart")

    def _on_Notification(self, ev):
        worker = self._worker(ev, role="lead")
        kind = ev.get("detail", "notification")
        if kind == "permission_prompt":
            worker["station"] = "reception"
            worker["status"] = "waiting"
            worker["activity"] = "waiting on a signature"
            worker["since"] = ev["ts"]
            self._note(ev, worker, "waits at reception for a signature", kind="wait")
        elif kind == "idle_prompt":
            worker["station"] = "break_room"
            worker["status"] = "idle"
            worker["activity"] = "waiting for the next order"
            worker["since"] = ev["ts"]
            self._note(ev, worker, "steps into the break room", kind="idle")
        else:
            self._note(ev, worker, kind.replace("_", " "), kind="info")

    def _on_Stop(self, ev):
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, "handing the work back", status="idle")
        for ticket in self.tickets:
            if ticket["session"] == ev["session"] and ticket["status"] == "open":
                ticket["status"] = "done"
                ticket["closed"] = ev["ts"]
        self._note(ev, worker, "turns the finished work back over the counter", kind="done")

    def _on_StopFailure(self, ev):
        worker = self._worker(ev, role="lead")
        self.stats["errors"] += 1
        self._park(worker, ev, "line went dead: {}".format(ev.get("detail", "error")), status="blocked")
        self._note(ev, worker, "the line goes dead ({})".format(ev.get("detail", "error")), kind="error")

    def _on_PreCompact(self, ev):
        worker = self._worker(ev, role="lead")
        worker["station"] = "records"
        worker["status"] = "working"
        worker["activity"] = "boxing up old paperwork"
        worker["since"] = ev["ts"]
        self._note(ev, worker, "boxes up old paperwork for the archive", kind="info")

    def _on_PostCompact(self, ev):
        worker = self._worker(ev, role="lead")
        self._park(worker, ev, "desk cleared")
        self._note(ev, worker, "comes back to a cleared desk", kind="info")

    # -- output ----------------------------------------------------------

    def snapshot(self, now=None):
        now = float(now if now is not None else time.time())
        workers = []
        for worker in self.workers.values():
            left = worker.get("left")
            if worker["status"] == "gone" and left and now - left > DEPARTURE_GRACE_SECONDS:
                continue
            item = dict(worker)
            item["busy_for"] = max(0.0, now - float(worker.get("since") or now))
            workers.append(item)
        workers.sort(key=lambda w: (not w["lead"], w["desk"]))

        stations = {}
        for name, meta in STATIONS.items():
            occupants = [w["name"] for w in workers if w["station"] == name and w["status"] != "gone"]
            stations[name] = {
                "label": meta["label"],
                "blurb": meta["blurb"],
                "occupants": occupants,
                "uses": self.stats["by_station"].get(name, 0),
            }

        busiest = sorted(self.stats["by_tool"].items(), key=lambda kv: -kv[1])[:6]
        active = [w for w in workers if w["status"] not in ("gone",)]
        return {
            "v": SCHEMA_VERSION,
            "now": now,
            "since": self.first_ts,
            "last_event": self.last_ts,
            "workers": workers,
            "stations": stations,
            "tickets": list(reversed(self.tickets[-TICKET_LIMIT:])),
            "log": self.log[-80:],
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


def build(events, now=None):
    """Convenience: fold an iterable of events into a snapshot."""
    office = Office()
    for ev in sorted(events, key=lambda e: float(e.get("ts") or 0)):
        office.apply(ev)
    return office.snapshot(now=now)

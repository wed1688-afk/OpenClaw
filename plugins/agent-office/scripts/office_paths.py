"""Where the Agent Office keeps its paperwork.

Everything lives under a single directory, by default `~/.claude/agent-office`:

    events/2026-09-20.jsonl   one append-only ledger per day
    server.json               pid/port of the running office server

Override with `AGENT_OFFICE_HOME`.

The path must be the same for the recorder and the server, and those run in
very different processes: the recorder is a hook, which Claude Code gives
`CLAUDE_PLUGIN_DATA`, while the server is started from an ordinary shell,
which is given no such thing.  Keying off that variable therefore splits the
two apart -- the hooks write to the plugin data directory and the office reads
an empty one -- so this deliberately ignores it and uses a location any process
can work out on its own.  It sits outside the plugin, so plugin updates leave
it alone either way.
"""

from __future__ import annotations

import os
import time

DAY_FORMAT = "%Y-%m-%d"


def home():
    override = os.environ.get("AGENT_OFFICE_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), ".claude", "agent-office")


def events_dir():
    return os.path.join(home(), "events")


def ledger_path(when=None):
    stamp = time.strftime(DAY_FORMAT, time.localtime(when if when is not None else time.time()))
    return os.path.join(events_dir(), stamp + ".jsonl")


def ensure_dirs():
    os.makedirs(events_dir(), exist_ok=True)
    return home()


def ledgers(days=2):
    """Most recent ledger files, oldest first (at most `days` of them)."""
    directory = events_dir()
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".jsonl"))
    except OSError:
        return []
    return [os.path.join(directory, n) for n in names[-max(1, days):]]


def prune(keep_days=7):
    """Drop ledgers older than `keep_days`; best effort, never raises."""
    directory = events_dir()
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".jsonl"))
    except OSError:
        return []
    doomed = names[:-keep_days] if len(names) > keep_days else []
    removed = []
    for name in doomed:
        try:
            os.remove(os.path.join(directory, name))
            removed.append(name)
        except OSError:
            pass
    return removed


def runtime_path():
    return os.path.join(home(), "server.json")

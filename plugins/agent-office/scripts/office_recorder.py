#!/usr/bin/env python3
"""Claude Code hook entrypoint: write one line per lifecycle event.

This runs on every session start, prompt, tool call and subagent, so it is
built to be boring: read stdin, append a short JSON line, exit 0.  It never
prints to stdout (which Claude would read as a hook decision), never blocks a
tool call, and swallows its own errors -- a broken visualizer must not be able
to break a coding session.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import office_paths  # noqa: E402
import office_state  # noqa: E402

MAX_INPUT_BYTES = 1 << 20  # 1 MiB of hook payload is already absurd
MAX_LINE_BYTES = 4000  # stay inside the atomic-append window for O_APPEND


def append(record, path=None):
    """Append one event to today's ledger. Returns the path written."""
    path = path or office_paths.ledger_path(record.get("ts"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        trimmed = dict(record)
        for key in ("detail", "activity", "target"):
            if key in trimmed:
                trimmed[key] = str(trimmed[key])[:200]
        line = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":"))
        line = line[:MAX_LINE_BYTES]
        if not line.endswith("}"):
            # Never leave a half-written object on the ledger.
            line = json.dumps(
                {k: record[k] for k in ("v", "ts", "event", "session") if k in record},
                separators=(",", ":"),
            )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return path


def read_payload(stream=None):
    stream = stream if stream is not None else sys.stdin
    try:
        raw = stream.read(MAX_INPUT_BYTES)
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv=None, stream=None):
    payload = read_payload(stream)
    if not payload.get("hook_event_name"):
        # Nothing recognisable on stdin -- record nothing rather than a row of
        # "Unknown" that would clutter the floor.
        return 0
    record = office_state.normalize(payload)
    append(record)
    if record.get("event") == "SessionStart":
        office_paths.prune(keep_days=7)
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a visualizer must never break the session
        pass
    sys.exit(0)

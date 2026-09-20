#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed a believable office so the view can be looked at without waiting.

`office_server.py demo` calls `seed()` and then serves the result.  The script
writes the same records the hooks would write, backdated over the last few
minutes, and deliberately leaves two clerks mid-task so the floor is moving.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import office_paths  # noqa: E402
import office_recorder  # noqa: E402
import office_state  # noqa: E402

SESSION = "demo-session"
SUB_EXPLORE = "demo-explore"
SUB_PLAN = "demo-plan"


def _script():
    """(seconds before now, hook payload) -- the story the office is telling."""
    base = dict(session_id=SESSION, cwd=os.getcwd())
    sub = dict(base, agent_id=SUB_EXPLORE, agent_type="Explore")
    plan = dict(base, agent_id=SUB_PLAN, agent_type="Plan")

    def tool(where, name, tool_input, ok=True, seconds_open=6):
        """A complete tool call: PreToolUse now, PostToolUse a little later."""
        return [
            (where, "PreToolUse", {"tool_name": name, "tool_input": tool_input}),
            (
                where - seconds_open,
                "PostToolUseFailure" if not ok else "PostToolUse",
                {"tool_name": name, "tool_input": tool_input, "tool_response": {"text": "ok" if ok else "2 tests failed"}},
            ),
        ]

    steps = [
        (300, "SessionStart", {"session_start_type": "startup"}),
        (298, "UserPromptSubmit", {"prompt": "把辦公室狀態用 SSE 推出去，帳本維持只追加", "source": "direct"}),
    ]
    steps += tool(292, "Read", {"file_path": "plugins/agent-office/scripts/office_server.py"})
    steps += tool(280, "Grep", {"pattern": "def serve", "path": "scripts"})
    steps += tool(268, "Task", {"description": "盤點帳本的讀取端", "prompt": "找出所有讀取帳本的地方"})

    flat = [(offset, dict(base, hook_event_name=event, **fields)) for offset, event, fields in steps]

    # A researcher arrives, does two jobs, and clocks out.
    flat.append((266, dict(sub, hook_event_name="SubagentStart")))
    for offset, event, fields in tool(262, "Glob", {"pattern": "**/*.py"}) + tool(250, "Read", {"file_path": "scripts/office_state.py"}):
        flat.append((offset, dict(sub, hook_event_name=event, **fields)))
    flat.append((238, dict(sub, hook_event_name="SubagentStop", last_assistant_message="三處讀取端，都在 office_server.py")))

    # Meanwhile the lead writes code, trips over a test, and fixes it.
    tail = []
    tail += tool(230, "Edit", {"file_path": "scripts/office_server.py"})
    tail += tool(214, "Write", {"file_path": "web/office.js"})
    tail += tool(196, "Bash", {"command": "python3 -m unittest -q", "description": "跑測試"}, ok=False, seconds_open=12)
    tail += tool(170, "Edit", {"file_path": "scripts/office_state.py"})
    tail += tool(150, "Bash", {"command": "python3 -m unittest -q", "description": "跑測試"}, seconds_open=11)
    for offset, event, fields in tail:
        flat.append((offset, dict(base, hook_event_name=event, **fields)))

    flat.append((120, dict(base, hook_event_name="Notification", notification_type="permission_prompt")))
    flat.append((112, dict(base, hook_event_name="UserPromptSubmit", prompt="看起來不錯，接著把樓面畫出來", source="direct")))

    # An architect who is still here, still working.
    flat.append((96, dict(plan, hook_event_name="SubagentStart")))
    for offset, event, fields in tool(90, "Read", {"file_path": "web/index.html"}):
        flat.append((offset, dict(plan, hook_event_name=event, **fields)))
    flat.append((40, dict(plan, hook_event_name="PreToolUse", tool_name="Write", tool_input={"file_path": "web/office.css"})))

    # ...and the lead, mid-command, right now.
    flat.append((8, dict(base, hook_event_name="PreToolUse", tool_name="Bash",
                         tool_input={"command": "python3 scripts/office_server.py state --brief", "description": "看看樓面"})))
    return flat


def seed(now=None):
    """Write the demo ledger. Returns the number of events written."""
    now = float(now if now is not None else time.time())
    office_paths.ensure_dirs()
    written = 0
    for offset, payload in sorted(_script(), key=lambda item: -item[0]):
        ts = now - offset
        record = office_state.normalize(payload, now=ts)
        office_recorder.append(record, path=office_paths.ledger_path(ts))
        written += 1
    return written


if __name__ == "__main__":
    print("wrote {} events to {}".format(seed(), office_paths.events_dir()))

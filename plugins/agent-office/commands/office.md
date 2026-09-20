---
description: Open the Agent Office — a live view of this session as clerks working in an office
argument-hint: "[open | stop | status | state | demo]"
allowed-tools: Bash
---

The user ran `/office $ARGUMENTS`. An empty argument means `open`.

The office server is standard-library Python and reads only the local hook
ledger. Run it as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/office_server.py" <sub>`:

- **open** — start `serve` **in the background** (`run_in_background: true` on the
  Bash call, so the turn is not blocked). Wait a moment, then read the command's
  output and tell the user the URL it printed (normally <http://127.0.0.1:4269/>).
  If the output says the office is already open, just give them that URL.
- **stop** — run `stop` in the foreground and confirm it closed.
- **status** — run `status` in the foreground and report it.
- **state** — run `state --brief` in the foreground and summarize who is on the
  floor and what they are doing, in prose. Use this when the user wants the
  answer in the terminal rather than a browser.
- **en** / **zh** (or any `--lang` the user names) — pass it through, e.g.
  `serve --lang en` or `state --brief --lang en`. The view is in Traditional
  Chinese by default and also has a language selector in the top right.
- **demo** — start `demo` **in the background**; it seeds a staged shift in a
  separate `demo` ledger and serves that, so real session data is untouched.

Notes worth passing on when relevant:

- The floor only fills up once the plugin's hooks have fired, so a brand-new
  session looks quiet until the first tool call.
- Nothing leaves the machine: the server binds `127.0.0.1` and the ledger lives
  under `~/.claude/agent-office` (or `${CLAUDE_PLUGIN_DATA}`).
- Answer the user in whichever language they are writing in.

Do not edit plugin files as part of this command unless the user asks.

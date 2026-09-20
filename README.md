# OpenClaw

An agent workspace. The tree is mostly scaffolding so far — `agents/`,
`backend/`, `tools/` and `memory/` are placeholders — plus one thing that works
end to end:

## Agent Office

A Claude Code plugin that shows what Claude Code is doing as a floor of clerks in
an office. Tool calls become people walking to the records room, the drafting
table or the server room; subagents are new hires who take a desk and clock out
when they are done.

```
/plugin marketplace add wed1688-afk/openclaw
/plugin install agent-office@openclaw
/office
```

See [`plugins/agent-office/README.md`](plugins/agent-office/README.md) for the
floor plan, the event-to-room mapping, what is recorded (and what is redacted),
and how the pieces fit together.

## Tests

```
python3 -m unittest discover -s tests
```

No third-party packages are needed for the plugin or its tests — the office is
standard library and plain JavaScript on a canvas.

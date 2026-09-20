#!/usr/bin/env python3
"""Serve the Agent Office: a live view of what Claude Code is doing.

    office_server.py serve [--port 4269] [--days 1] [--open]
    office_server.py status
    office_server.py stop
    office_server.py state        # print a JSON snapshot and exit
    office_server.py demo         # seed a fake office and serve it

Standard library only, binds to 127.0.0.1, and reads nothing but the local
event ledger written by the hooks.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import office_paths  # noqa: E402
import office_state  # noqa: E402
import office_text  # noqa: E402

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_ROOT = os.path.join(PLUGIN_ROOT, "web")
DEFAULT_PORT = int(os.environ.get("AGENT_OFFICE_PORT") or 4269)
POLL_SECONDS = 0.4
FORCE_PUSH_SECONDS = 5.0
KEEPALIVE_SECONDS = 15.0


# --------------------------------------------------------------------------
# Incremental ledger reader
# --------------------------------------------------------------------------


class Ledger:
    """Tails the JSONL ledgers and keeps an Office folded up to date."""

    def __init__(self, days=1):
        self.days = max(1, int(days))
        self.lock = threading.Lock()
        self.office = office_state.Office()
        self.offsets = {}
        self.revision = 0
        self.refresh()

    def _paths(self):
        return office_paths.ledgers(days=self.days)

    def refresh(self):
        """Read whatever is new; bump the revision if anything arrived."""
        applied = 0
        with self.lock:
            for path in self._paths():
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                offset = self.offsets.get(path, 0)
                if size < offset:  # file was rotated or truncated underneath us
                    offset = 0
                if size == offset:
                    continue
                try:
                    with open(path, "rb") as fh:
                        fh.seek(offset)
                        chunk = fh.read()
                except OSError:
                    continue
                # Keep any trailing partial line for the next pass.
                cut = chunk.rfind(b"\n")
                if cut == -1:
                    continue
                self.offsets[path] = offset + cut + 1
                for line in chunk[:cut].splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    self.office.apply(event)
                    applied += 1
            if applied:
                self.revision += 1
        return applied

    def snapshot(self, locale=None):
        with self.lock:
            state = self.office.snapshot(locale=locale)
            state["revision"] = self.revision
            state["home"] = office_paths.home()
            state["days"] = self.days
            return state


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentOffice/0.1"
    ledger = None  # injected by serve()

    def log_message(self, fmt, *args):  # quiet by default
        if os.environ.get("AGENT_OFFICE_VERBOSE"):
            sys.stderr.write("[office] " + (fmt % args) + "\n")

    # -- helpers ---------------------------------------------------------

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def _send_file(self, relative):
        safe = os.path.normpath(relative).lstrip("/\\")
        path = os.path.join(WEB_ROOT, safe)
        if not os.path.abspath(path).startswith(os.path.abspath(WEB_ROOT) + os.sep):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(path):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ctype, _ = mimetypes.guess_type(path)
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype or "application/octet-stream")

    # -- routes ----------------------------------------------------------

    def _locale(self):
        query = parse_qs(urlparse(self.path).query)
        requested = (query.get("lang") or [None])[0]
        return office_text.normalize_locale(requested)

    def do_GET(self):  # noqa: N802  (http.server API)
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file("index.html")
        if path == "/api/health":
            return self._send_json({"ok": True, "pid": os.getpid(), "home": office_paths.home()})
        if path == "/api/state":
            self.ledger.refresh()
            return self._send_json(self.ledger.snapshot(locale=self._locale()))
        if path == "/api/stream":
            return self._stream()
        return self._send_file(path.lstrip("/"))

    def _stream(self):
        locale = self._locale()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_revision = -1
        last_push = 0.0
        last_keepalive = time.time()
        try:
            while not self.server.stopping:
                self.ledger.refresh()
                state = self.ledger.snapshot(locale=locale)
                now = time.time()
                changed = state["revision"] != last_revision
                if changed or now - last_push >= FORCE_PUSH_SECONDS:
                    payload = json.dumps(state, ensure_ascii=False)
                    self.wfile.write(b"event: state\ndata: " + payload.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    last_revision = state["revision"]
                    last_push = now
                    last_keepalive = now
                elif now - last_keepalive >= KEEPALIVE_SECONDS:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_keepalive = now
                time.sleep(POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    stopping = False


# --------------------------------------------------------------------------
# Runtime bookkeeping
# --------------------------------------------------------------------------


def write_runtime(port):
    office_paths.ensure_dirs()
    info = {
        "pid": os.getpid(),
        "port": port,
        "url": "http://127.0.0.1:{}/".format(port),
        "started": time.time(),
        "home": office_paths.home(),
    }
    with open(office_paths.runtime_path(), "w") as fh:
        json.dump(info, fh, indent=2)
    return info


def read_runtime():
    try:
        with open(office_paths.runtime_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def clear_runtime():
    try:
        os.remove(office_paths.runtime_path())
    except OSError:
        pass


def bind(port, tries=20):
    last = None
    for offset in range(tries):
        try:
            return Server(("127.0.0.1", port + offset), Handler), port + offset
        except OSError as exc:
            last = exc
    raise SystemExit("could not bind a port near {}: {}".format(port, last))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_serve(args):
    existing = read_runtime()
    if existing and alive(existing.get("pid")) and existing.get("pid") != os.getpid():
        print("The office is already open at {} (pid {}).".format(existing.get("url"), existing.get("pid")))
        return 0

    if getattr(args, "lang", None):
        os.environ["AGENT_OFFICE_LANG"] = office_text.normalize_locale(args.lang)
    office_paths.ensure_dirs()
    Handler.ledger = Ledger(days=args.days)
    httpd, port = bind(args.port)
    info = write_runtime(port)

    def shutdown(_signum=None, _frame=None):
        httpd.stopping = True
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except (ValueError, OSError):
            pass

    print("Agent Office open at {}".format(info["url"]))
    print("Reading the ledger in {}".format(office_paths.events_dir()))
    sys.stdout.flush()

    if args.open:
        import webbrowser

        threading.Thread(target=lambda: webbrowser.open(info["url"]), daemon=True).start()

    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.stopping = True
        clear_runtime()
        httpd.server_close()
    return 0


def cmd_status(args):
    info = read_runtime()
    if not info or not alive(info.get("pid")):
        if info:
            clear_runtime()
        print("The office is closed. Open it with: /office")
        return 1
    print("Agent Office is open at {} (pid {}).".format(info.get("url"), info.get("pid")))
    return 0


def cmd_stop(args):
    info = read_runtime()
    if not info or not alive(info.get("pid")):
        clear_runtime()
        print("The office was already closed.")
        return 0
    try:
        os.kill(int(info["pid"]), signal.SIGTERM)
    except OSError as exc:
        print("Could not stop pid {}: {}".format(info.get("pid"), exc))
        return 1
    clear_runtime()
    print("Closed the office at {}.".format(info.get("url")))
    return 0


def _pad(text, width):
    """Pad to a visual width, counting wide CJK glyphs as two columns."""
    import unicodedata

    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(text))
    return str(text) + " " * max(0, width - shown)


def cmd_state(args):
    ledger = Ledger(days=args.days)
    state = ledger.snapshot(locale=getattr(args, "lang", None))
    if args.brief:
        stats = state["stats"]
        workers = [w for w in state["workers"] if w["status"] != "gone"]
        print("headcount {} | busy {} | tool calls {} | errors {} | tickets {}".format(
            stats["headcount"], stats["busy"], stats["tool_calls"], stats["errors"], len(state["tickets"])))
        for worker in workers:
            print("  {} {} {} {}".format(
                _pad(worker["name"], 10), _pad(worker["title"], 12),
                _pad(worker["station"], 12), worker["activity"]))
    else:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def cmd_demo(args):
    home = os.environ.get("AGENT_OFFICE_HOME") or os.path.join(office_paths.home(), "demo")
    os.environ["AGENT_OFFICE_HOME"] = home
    office_paths.ensure_dirs()
    import office_demo

    written = office_demo.seed()
    print("Seeded {} demo events in {}".format(written, office_paths.events_dir()))
    return cmd_serve(args)


def build_parser():
    parser = argparse.ArgumentParser(description="Serve the Agent Office view of Claude Code's work.")
    sub = parser.add_subparsers(dest="command")

    def add_serve_args(p):
        p.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to bind (default %(default)s)")
        p.add_argument("--days", type=int, default=1, help="how many days of ledger to replay")
        p.add_argument("--open", action="store_true", help="open a browser once the server is up")
        p.add_argument("--lang", default=None, help="default language for the view (zh-Hant, en)")

    serve = sub.add_parser("serve", help="run the office server (default)")
    add_serve_args(serve)
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="seed a fake office and serve it")
    add_serve_args(demo)
    demo.set_defaults(func=cmd_demo)

    status = sub.add_parser("status", help="is the office open?")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="close the office")
    stop.set_defaults(func=cmd_stop)

    state = sub.add_parser("state", help="print the current floor plan")
    state.add_argument("--days", type=int, default=1)
    state.add_argument("--brief", action="store_true", help="one line per worker instead of JSON")
    state.add_argument("--lang", default=None, help="language to word the floor in (zh-Hant, en)")
    state.set_defaults(func=cmd_state)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "serve")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

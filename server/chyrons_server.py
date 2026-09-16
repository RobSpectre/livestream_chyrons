#!/usr/bin/env python3
"""Live chyron telemetry service for the Vue overlay.

Serves the built overlay and a JSON telemetry endpoint that reads the exact
same sources the OpenDeck "Page 6" buttons read:

  * Codex   -> tools.codex_telemetry.Telemetry   (CodexAttention IPC + rollouts + app-server quota)
  * Claude  -> tools.agent_telemetry.AgentTelemetry (Claude Code JSONL transcripts + status hooks)
  * Hermes  -> tools.agent_telemetry.AgentTelemetry (Hermes state.db + CLI bridge session cache)

Only local read-only state is touched; nothing here writes to the agents.
"""
import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
STREAMDECK = Path(os.environ.get('STREAMDECK_ROOT', '/media/rspectre/Storage/workspace/streamdeck'))
DIST = ROOT / 'dist'

sys.path.insert(0, str(STREAMDECK))
sys.path.insert(0, str(ROOT / 'server'))  # agent_extras, obs_reflow: importable by path too
from agent_extras import Extras  # noqa: E402  (after the path setup above)
from obs_reflow import Reflow, websocket_config  # noqa: E402

# Page 6 metric order: turn state, tokens/sec, context left, session, 30-day, quota.
# `quota` stays in the payload (the deck keys still use it) even though the chyron
# no longer renders a quota capsule.
METRICS = ('activity', 'speed', 'context', 'session', 'month', 'quota')
# Overlay-only capsules: model name and a 1-hour token sparkline.
EXTRAS = ('model', 'spark')
# Banner identity, in the order the chyrons stack from the top.
AGENTS = ('codex', 'claude', 'hermes')
# The SSE loop wakes this often to notice a visibility change early, so hiding
# the chyrons silences the tally within ~100ms instead of up to a second.
STREAM_SLICE_SECONDS = 0.1
STREAM_SLICES = 10
BRANDS = {
    # accent = block colour of the chips and capsules, tint = the light card the
    # blocks sit on (the library's light-card idiom: black frames and hard black
    # shadows only read against something lighter), ink = text on the accent.
    'codex': {'label': 'CODEX', 'accent': '#ffffff', 'tint': '#dcdcdc', 'ink': '#000000',
              'sources': 'Codex CLI + desktop IPC'},
    'claude': {'label': 'CLAUDE', 'accent': '#d97757', 'tint': '#f7e4dd', 'ink': '#000000',
               'sources': 'Claude Code transcripts'},
    'hermes': {'label': 'HERMES', 'accent': '#0000f2', 'tint': '#ccccfc', 'ink': '#ffffff',
               'sources': 'Hermes state.db + CLI bridge'},
}


def metric_rows(values):
    """Convert a plugin metric mapping into the overlay payload."""
    rows = {}
    for name in METRICS:
        value = values.get(name)
        if not value:
            continue
        title, number, detail, color = value
        rows[name] = {'title': title, 'value': number, 'detail': detail, 'color': color}
    return rows


def extra_rows(extras, accent, ink):
    """Model + sparkline capsules, tinted with the banner accent."""
    rows = {}
    for name in EXTRAS:
        value = extras.get(name)
        if not value:
            continue
        rows[name] = dict(value, color=accent, ink=ink)
    return rows


class Snapshot:
    """Reads the same telemetry objects the OpenDeck plugin holds."""

    def __init__(self, streamdeck=STREAMDECK, enabled=None):
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.error = None
        self.agents = [a for a in BRANDS if enabled is None or a in enabled]
        self.attention = None
        self.codex = None
        self.agents_telemetry = None
        self.extras = None
        self.cache = {}
        self.cache_at = 0
        self.streamdeck = streamdeck
        threading.Thread(target=self.prepare, daemon=True).start()

    def prepare(self):
        try:
            sys.path.insert(0, str(self.streamdeck))
            from tools.codex_telemetry import Telemetry
            from tools.agent_telemetry import AgentTelemetry
            from tools.codex_attention import CodexAttention
            if 'codex' in self.agents:
                self.attention = CodexAttention()
                self.codex = Telemetry(self.attention)
            if 'claude' in self.agents or 'hermes' in self.agents:
                self.agents_telemetry = AgentTelemetry()
            home = Path.home()
            self.extras = Extras(codex_root=home / '.codex', claude_root=home / '.claude',
                                 hermes_db=home / '.hermes/state.db')
        except Exception as error:  # noqa: BLE001 - surfaced to the overlay as status
            with self.lock:
                self.error = f'{type(error).__name__}: {error}'
        finally:
            self.ready.set()

    def collect(self):
        payload = []
        # The parsed readers the telemetry objects already hold: reusing them keeps
        # the hourly series identical to the numbers on the deck.
        readers = getattr(self.codex, 'readers', {}) if self.codex else {}
        files = getattr(self.agents_telemetry, 'files', {}) if self.agents_telemetry else {}
        for name in self.agents:
            if name == 'codex':
                values = self.codex.values() if self.codex else {}
            else:
                values = self.agents_telemetry.values(name) if self.agents_telemetry else {}
            brand = BRANDS[name]
            extras = {}
            if self.extras:
                try:
                    extras = self.extras.metrics(name, readers, files)
                except Exception:  # noqa: BLE001 - extras must never blank a banner
                    extras = {}
            payload.append({'key': name, 'label': brand['label'], 'accent': brand['accent'],
                            'tint': brand['tint'], 'ink': brand['ink'],
                            'source': brand['sources'], 'metrics': metric_rows(values),
                            'extras': extra_rows(extras, brand['accent'], brand['ink'])})
        return {'generated': time.time(), 'ready': True, 'error': self.error, 'agents': payload}

    def get(self, max_age=0.5):
        """Coalesce concurrent requests onto one telemetry read."""
        with self.lock:
            if time.monotonic() - self.cache_at < max_age and self.cache:
                return dict(self.cache, cached=True)
        # First request waits for the reader threads to exist rather than
        # recursing, so a failed import can never spin the request thread.
        if not self.ready.wait(15):
            return {'generated': time.time(), 'ready': False,
                    'error': 'telemetry readers still starting', 'agents': []}
        if self.codex is None and self.agents_telemetry is None:
            # Startup failure: report it once instead of spinning for a reader.
            return {'generated': time.time(), 'ready': False, 'error': self.error, 'agents': []}
        data = self.collect()
        with self.lock:
            self.cache, self.cache_at = data, time.monotonic()
        return dict(data, cached=False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    snapshot = None
    dist = DIST
    reflow = None
    # Last audio report from the page playing the tally, per page identity, so an
    # operator (and the test suite) can see whether OBS's embedded browser is
    # actually running the audio graph rather than leaving it suspended. The
    # audio-only source is the one whose silence matters.
    tally_reports = {}

    def log_message(self, *args):  # keep the stream console quiet
        pass

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        kind = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/api/tally-state':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            report = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, TypeError):
            self.send_error(400)
            return
        report['received'] = time.time()
        page = str(report.get('page') or 'overlay')[:24]
        # One slot per page: a test browser playing tallies must not be mistaken
        # for the OBS audio source whose silence is being measured.
        type(self).tally_reports[page] = report
        self.send_json({'ok': True})

    def tally(self):
        """The audio-only source's report, else whichever page reported last."""
        reports = type(self).tally_reports or {}
        if not reports:
            return None
        return reports.get('audio-only') or max(reports.values(), key=lambda r: r['received'])

    def visibility(self):
        # type(self), not Handler: a subclass must be able to stand in for it.
        reflow = type(self).reflow
        if reflow is None:
            return {'on_air': True, 'agents': {a: True for a in AGENTS}}
        return dict(reflow.visibility)

    def telemetry(self):
        """Snapshot plus live visibility, so the tally knows what is on screen.

        Visibility is merged here rather than in the snapshot cache because it
        changes within ~50ms of a scene switch while the cache lives 500ms.
        """
        return dict(self.snapshot.get(), visibility=self.visibility())

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/api/telemetry':
            self.send_json(self.telemetry())
            return
        if path == '/api/health':
            data = self.snapshot.get(max_age=0)
            reflow = type(self).reflow
            self.send_json({'ok': bool(data.get('ready', True) and not data.get('error')),
                            'agents': [a['key'] for a in data.get('agents', [])],
                            'tally': self.tally(),
                            'tally_reports': dict(type(self).tally_reports or {}),
                            'reflow': reflow.status() if reflow else {'state': 'off'},
                            'error': data.get('error')})
            return
        if path == '/api/stream':
            self.stream()
            return
        if path in ('/', '/index.html'):
            self.send_file(self.dist / 'index.html')
            return
        target = (self.dist / path.lstrip('/')).resolve()
        if not str(target).startswith(str(self.dist.resolve())):
            self.send_error(403)
            return
        if target.is_file():
            self.send_file(target)
            return
        self.send_file(self.dist / 'index.html')  # SPA fallback

    def stream(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

        def emit():
            data = self.telemetry()
            self.wfile.write(f'data: {json.dumps(data)}\n\n'.encode())
            self.wfile.flush()
            return json.dumps(data.get('visibility'))

        try:
            previous = emit()
            while True:
                # Push the moment visibility changes - the tally has to go quiet
                # as soon as the scene leaves the program - and otherwise send
                # the usual one-second heartbeat.
                for _ in range(STREAM_SLICES):
                    time.sleep(STREAM_SLICE_SECONDS)
                    if json.dumps(self.visibility()) != previous:
                        break
                previous = emit()
        except (BrokenPipeError, ConnectionResetError):
            return


def main():
    parser = argparse.ArgumentParser(description='Serve the chyron overlay and its telemetry API.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--agents', default='codex,claude,hermes')
    parser.add_argument('--dist', default=str(DIST))
    parser.add_argument('--no-reflow', action='store_true',
                        help='do not keep the Chryons scene stacked from the top')
    args = parser.parse_args()
    if not (STREAMDECK / 'tools').is_dir():
        parser.error(f'Stream Deck tooling not found at {STREAMDECK}/tools (set STREAMDECK_ROOT)')
    Handler.snapshot = Snapshot(streamdeck=STREAMDECK,
                                enabled=[a.strip() for a in args.agents.split(',') if a.strip()])
    Handler.dist = Path(args.dist)
    if not args.no_reflow:
        config = websocket_config()
        if config:
            lock = STREAMDECK / '.obs-background.lock'
            Handler.reflow = Reflow(lock_path=lock if STREAMDECK.is_dir() else None).start(config)
        else:
            print('chyron reflow: no OBS websocket config found, disabled', flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'chyrons overlay on http://{args.host}:{args.port}/  (api: /api/telemetry)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

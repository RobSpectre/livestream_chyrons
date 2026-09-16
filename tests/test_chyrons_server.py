"""Tests for the chyron telemetry service (no agents or network needed)."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chyron_server', ROOT / 'server/chyrons_server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FakeTelemetry:
    """Stands in for tools.codex_telemetry.Telemetry / AgentTelemetry."""

    def __init__(self, values):
        self._values = values

    def values(self, name=None):
        return self._values


class FakeExtras:
    """Stands in for server/agent_extras.Extras."""

    def metrics(self, agent, codex_readers=None, claude_files=None, now=None):
        return {
            'model': {'title': 'MODEL', 'value': 'GPT 5.6 LUNA', 'detail': ''},
            'spark': {'title': '1H TOK', 'value': '4.8K', 'detail': '',
                      'series': [200] * 24},
        }


class MetricRowTests(unittest.TestCase):
    def test_metric_rows_keep_page_6_order_and_drop_missing(self):
        values = {
            'quota': ('QUOTA LEFT', '95%', 'WEEKLY', '#ffffff'),
            'activity': ('TURN', 'ACTIVE', 'LOCAL CODEX', '#ffffff'),
            'speed': ('TOKENS / SEC', '22.8', 'TURN AVG · OUTPUT', '#ffffff'),
        }
        rows = server.metric_rows(values)
        self.assertEqual(list(rows), ['activity', 'speed', 'quota'])
        self.assertEqual(rows['activity'], {'title': 'TURN', 'value': 'ACTIVE',
                                           'detail': 'LOCAL CODEX', 'color': '#ffffff'})

    def test_metric_rows_ignores_unknown_and_empty_values(self):
        self.assertEqual(server.metric_rows({'reset': ('RESET', '—', '', '#fff'), 'speed': None}), {})

    def test_brand_colours_match_the_stream_look(self):
        self.assertEqual(server.BRANDS['codex']['accent'], '#ffffff')
        self.assertEqual(server.BRANDS['claude']['accent'], '#d97757')
        self.assertEqual(server.BRANDS['hermes']['accent'], '#0000f2')

    def test_metric_keys_cover_the_requested_banners(self):
        self.assertEqual(server.METRICS, ('activity', 'speed', 'context', 'session', 'month', 'quota'))


class SnapshotTests(unittest.TestCase):
    def make(self, values=None):
        snapshot = server.Snapshot.__new__(server.Snapshot)
        snapshot.lock = threading.Lock()
        snapshot.ready = threading.Event()
        snapshot.ready.set()
        snapshot.error = None
        snapshot.agents = ['codex', 'claude', 'hermes']
        snapshot.codex = FakeTelemetry(values if values is not None else {
            'activity': ('TURN', 'ACTIVE', 'LOCAL CODEX', '#ffffff'),
            'quota': ('QUOTA LEFT', '95%', 'WEEKLY', '#ffffff')})
        snapshot.agents_telemetry = FakeTelemetry({'session': ('SESSION TOKENS', '4.1M', 'CLAUDE · LATEST LOCAL', '#d97757')})
        snapshot.extras = FakeExtras()
        snapshot.cache, snapshot.cache_at = {}, 0
        return snapshot

    def test_collect_emits_one_row_per_agent_with_accents(self):
        payload = self.make().collect()
        self.assertEqual([row['key'] for row in payload['agents']], ['codex', 'claude', 'hermes'])
        self.assertEqual([row['accent'] for row in payload['agents']],
                         ['#ffffff', '#d97757', '#0000f2'])

    def test_collect_adds_model_and_sparkline_capsules(self):
        rows = {row['key']: row for row in self.make().collect()['agents']}
        codex = rows['codex']['extras']
        self.assertEqual(codex['model']['value'], 'GPT 5.6 LUNA')
        self.assertEqual(codex['model']['color'], '#ffffff')
        self.assertEqual(len(codex['spark']['series']), 24)
        self.assertEqual(sum(codex['spark']['series']), 4800)
        self.assertEqual(codex['spark']['value'], '4.8K')

    def test_quota_stays_in_the_payload_although_the_chyron_hides_it(self):
        rows = {row['key']: row for row in self.make().collect()['agents']}
        self.assertIn('quota', rows['codex']['metrics'])

    def test_broken_extras_never_blank_a_banner(self):
        snapshot = self.make()

        class Boom:
            def metrics(self, *args, **kwargs):
                raise RuntimeError('no double-click and no graph either')

        snapshot.extras = Boom()
        rows = {row['key']: row for row in snapshot.collect()['agents']}
        self.assertEqual(rows['codex']['extras'], {})
        self.assertEqual(rows['codex']['metrics']['activity']['value'], 'ACTIVE')

    def test_get_coalesces_repeat_reads(self):
        snapshot = self.make()
        first = snapshot.get()
        second = snapshot.get()
        self.assertFalse(first.get('cached'))
        self.assertTrue(second.get('cached'))
        self.assertGreaterEqual(second['generated'], first['generated'])

    def test_failed_startup_reports_error_instead_of_hanging(self):
        snapshot = self.make()
        snapshot.codex = snapshot.agents_telemetry = None
        snapshot.error = 'ModuleNotFoundError: no PIL'
        payload = snapshot.get()
        self.assertFalse(payload['ready'])
        self.assertIn('no PIL', payload['error'])
        self.assertEqual(payload['agents'], [])


class HttpTests(unittest.TestCase):
    """Exercise the real request path: routing, JSON and static dist serving."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = SnapshotTests().make()
        cls.handler = type('Handler', (server.Handler,), {'snapshot': cls.snapshot, 'dist': ROOT / 'dist'})
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), cls.handler)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.headers.get('Content-Type'), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get('Content-Type'), error.read()

    def test_health_reports_ready_agents(self):
        status, kind, body = self.get('/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(kind, 'application/json')
        self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(json.loads(body)['agents'], ['codex', 'claude', 'hermes'])

    def test_telemetry_shape_matches_the_overlay_contract(self):
        status, _, body = self.get('/api/telemetry')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data['agents']), 3)
        for row in data['agents']:
            self.assertTrue({'key', 'label', 'accent', 'source', 'metrics', 'extras'} <= set(row))
            for metric in row['metrics'].values():
                self.assertTrue({'title', 'value', 'detail', 'color'} <= set(metric))
            self.assertTrue({'model', 'spark'} <= set(row['extras']))
            self.assertEqual(len(row['extras']['spark']['series']), 24)

    def test_index_is_served_from_dist(self):
        status, kind, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith('text/html'))
        self.assertIn(b'<div id="app">', body)

    def test_unknown_path_falls_back_to_the_overlay(self):
        status, _, body = self.get('/chyron')
        self.assertEqual(status, 200)
        self.assertIn(b'<div id="app">', body)

    def test_path_traversal_cannot_escape_dist(self):
        status, _, body = self.get('/../../etc/passwd')
        self.assertNotIn(b'root:', body)
        self.assertIn(status, (200, 403, 404))


class StubReflow:
    """Stands in for server/obs_reflow.Reflow: only what the HTTP layer reads."""

    def __init__(self, on_air=True, agents=None):
        self.state, self.scene, self.moves, self.error = 'running', 'Chryons', 0, None
        self.visibility = {'on_air': on_air,
                           'agents': agents or {a: True for a in server.AGENTS}}

    def status(self, positions=None):
        return {'state': self.state, 'scene': self.scene, 'moves': self.moves,
                'positions': {}, 'visibility': self.visibility, 'error': self.error}


class VisibilityHttpTests(unittest.TestCase):
    """The tally is told what is on screen, and it is told promptly."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = SnapshotTests().make()
        cls.reflow = StubReflow()
        cls.handler = type('Handler', (server.Handler,),
                           {'snapshot': cls.snapshot, 'dist': ROOT / 'dist',
                            'reflow': cls.reflow})
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), cls.handler)
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.reflow.visibility = {'on_air': True,
                                  'agents': {a: True for a in server.AGENTS}}

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return json.loads(response.read())

    def test_telemetry_carries_live_visibility(self):
        self.reflow.visibility = {'on_air': False,
                                  'agents': {'codex': True, 'claude': False, 'hermes': True}}
        data = self.get_json('/api/telemetry')
        self.assertFalse(data['visibility']['on_air'])
        self.assertFalse(data['visibility']['agents']['claude'])

    def test_health_exposes_visibility_for_the_operator(self):
        status = self.get_json('/api/health')['reflow']
        self.assertIn('visibility', status)
        self.assertTrue(status['visibility']['on_air'])

    def test_without_the_reflow_service_everything_stays_audible(self):
        handler = type('Handler', (server.Handler,),
                       {'snapshot': self.snapshot, 'dist': ROOT / 'dist', 'reflow': None})
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}/api/telemetry',
                                        timeout=10) as response:
                visibility = json.loads(response.read())['visibility']
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertTrue(visibility['on_air'])
        self.assertEqual(set(visibility['agents']), set(server.AGENTS))

    def test_a_test_browser_cannot_masquerade_as_the_audio_source(self):
        """A browser playing tallies on the harness must not hide the real source."""
        self.post_tally({'page': 'banner', 'plays': 5, 'state': 'running', 'volume': 0.14})
        self.post_tally({'page': 'audio-only', 'plays': 0, 'state': 'running', 'volume': 0.14})
        health = self.get_json('/api/health')
        self.assertEqual(health['tally']['plays'], 0, 'the audio source is the one that counts')
        self.assertEqual(health['tally']['page'], 'audio-only')
        self.assertEqual(sorted(health['tally_reports']), ['audio-only', 'banner'])

    def post_tally(self, report):
        request = urllib.request.Request(self.base + '/api/tally-state', method='POST',
                                         data=json.dumps(report).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_stream_pushes_the_change_instead_of_waiting_for_the_next_beat(self):
        """Hiding the chyrons has to silence the tally within a fraction of a second."""
        stream = urllib.request.urlopen(self.base + '/api/stream', timeout=10)
        try:
            first = json.loads(self.read_frame(stream))
            self.assertTrue(first['visibility']['on_air'])
            self.reflow.visibility = {'on_air': False,
                                      'agents': {a: True for a in server.AGENTS}}
            started = time.monotonic()
            second = json.loads(self.read_frame(stream))
            elapsed = time.monotonic() - started
        finally:
            stream.close()
        self.assertFalse(second['visibility']['on_air'])
        self.assertLess(elapsed, 0.75,
                        f'the stream held the change for {elapsed:.2f}s (heartbeat is 1s)')

    @staticmethod
    def read_frame(stream):
        while True:
            line = stream.readline()
            if not line:
                raise AssertionError('the stream ended before sending a frame')
            if line.startswith(b'data: '):
                return line[len(b'data: '):]


if __name__ == '__main__':
    unittest.main()

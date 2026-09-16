"""Tests for the model-name and hourly-series extras (no agents needed)."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
spec = importlib.util.spec_from_file_location('agent_extras', ROOT / 'server/agent_extras.py')
extras = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extras)


class CompactModelTests(unittest.TestCase):
    def test_real_model_names_fit_the_capsule(self):
        cases = {
            'claude-opus-5': 'OPUS 5',
            'claude-sonnet-4-5-20250929': 'SONNET 4.5',
            'claude-3-5-haiku-20241022': '3.5 HAIKU',
            'gpt-5.6-luna': 'GPT 5.6 LUNA',
            'gpt-6-astra': 'GPT 6 ASTRA',
            'deepseek-ai/DeepSeek-V4.1-Flash': 'DEEPSEEK V4.1',
            'codex-mini-latest': 'CODEX MINI',
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(extras.compact_model(name), expected)

    def test_labels_stay_short_and_shouty(self):
        for name in ['claude-opus-5', 'deepseek-ai/DeepSeek-V4.1-Flash',
                     'meta-llama/Llama-4-Scout-17B-16E-Instruct']:
            label = extras.compact_model(name)
            self.assertLessEqual(len(label), extras.MAX_LABEL, label)
            self.assertEqual(label, label.upper(), label)

    def test_missing_names_do_not_crash(self):
        self.assertIsNone(extras.compact_model(None))
        self.assertIsNone(extras.compact_model(''))


class BucketTests(unittest.TestCase):
    def test_events_land_in_half_bucket_sized_buckets_oldest_first(self):
        now = 1_800_000_000
        events = [(now - 10, 5), (now - 200, 7), (now - 3500, 11)]
        series = extras.bucket_series(events, now)
        self.assertEqual(len(series), 24)
        self.assertEqual(series[-1], 5)       # last 2.5 minutes
        self.assertEqual(series[-2], 7)       # 2.5-5 minutes ago
        self.assertEqual(series[0], 11)       # 57.5-60 minutes ago

    def test_events_outside_the_window_are_ignored(self):
        now = 1_800_000_000
        self.assertEqual(sum(extras.bucket_series([(now - 3601, 99), (now + 5, 99)], now)), 0)

    def test_interval_totals_spread_over_the_buckets_they_cover(self):
        now = 1_800_000_000
        series = extras.spread([(now - 600, now - 300, 1200)], now)
        self.assertEqual(sum(series), 1200)
        self.assertEqual(series[-3], 600)     # the 5-7.5 minute bucket, fully covered
        self.assertEqual(series[-4], 600)     # the 7.5-10 minute bucket, fully covered

    def test_interval_crossing_a_boundary_is_split(self):
        now = 1_800_000_000
        series = extras.spread([(now - 1800, now - 1500, 300)], now)
        self.assertEqual(sum(series), 300)
        self.assertTrue(all(0 <= value <= 300 for value in series))


class ExtrasTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.tmp = Path(self.enterContext(__import__('tempfile').TemporaryDirectory()))
        self.extras = extras.Extras(codex_root=self.tmp / 'codex', claude_root=self.tmp / 'claude',
                                    hermes_db=self.tmp / 'state.db')

    def test_readers_and_files_drive_the_series(self):
        reader = type('Reader', (), {'events': [(self.now - 30, 500), (self.now - 3000, 400)]})()
        metrics = self.extras.metrics('codex', {'a': reader}, {})
        self.assertEqual(len(metrics['spark']['series']), 24)
        self.assertEqual(metrics['spark']['value'], '900')

    def test_claude_transcripts_drive_the_series(self):
        files = {'x.jsonl': {'messages': {'m1': (self.now - 60, 1234, 20)}}}
        metrics = self.extras.metrics('claude', {}, files)
        self.assertEqual(metrics['spark']['series'][-1], 1234)
        self.assertEqual(metrics['model']['value'], '—')  # no transcripts on disk

    def test_hermes_series_comes_from_session_model_usage(self):
        with sqlite3.connect(self.tmp / 'state.db') as db:
            db.execute('CREATE TABLE sessions (id TEXT, parent_session_id TEXT, message_count INT, '
                       'model TEXT, started_at REAL, last_activity_at REAL)')
            db.execute('CREATE TABLE session_model_usage (session_id TEXT, model TEXT, first_seen REAL, '
                       'last_seen REAL, input_tokens INT, output_tokens INT, cache_read_tokens INT, '
                       'cache_write_tokens INT)')
            db.execute("INSERT INTO sessions VALUES ('s1', NULL, 5, 'deepseek-ai/DeepSeek-V4.1-Flash', ?, ?)",
                       (self.now - 600, self.now - 10))
            # 1000 in + 500 out + 4000 cache read + 300 cache write
            db.execute("INSERT INTO session_model_usage VALUES ('s1', 'x', ?, ?, 1000, 500, 4000, 300)",
                       (self.now - 600, self.now - 10))
        metrics = self.extras.metrics('hermes')
        self.assertEqual(metrics['model']['value'], 'DEEPSEEK V4.1')
        self.assertEqual(sum(metrics['spark']['series']), 5800)

    def test_model_lookup_is_cached_between_calls(self):
        calls = []

        def fake_model(agent):
            calls.append(agent)
            return 'CACHED'

        self.extras.model = fake_model
        self.extras.models = {'codex': 'gpt-5.6-luna'}
        self.extras.models_at = time.monotonic()
        self.assertEqual(extras.Extras.model(self.extras, 'codex'), 'GPT 5.6 LUNA')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()

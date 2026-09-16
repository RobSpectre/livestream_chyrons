#!/usr/bin/env python3
"""Extra per-agent facts for the chyron overlay: model name and hourly token shape.

Read-only, and built from the same sources the OpenDeck Page 6 telemetry keys
use. Two of the three agents keep a real per-event history, so the hourly series
is exact for them:

  * Codex  -> the rollout token_count events that tools.codex_telemetry already
              parses (Telemetry.readers[*].events).
  * Claude -> the assistant-message usage records in the transcripts that
              tools.agent_telemetry already parses (AgentTelemetry.files).
  * Hermes -> state.db keeps no per-message token history, only one row per
              (session, model, task) in session_model_usage with a first_seen /
              last_seen interval. Those totals are spread across the buckets
              they cover, proportionally to the overlap, so the shape is an
              estimate for Hermes and exact for the other two.
"""
import json
from pathlib import Path
import re
import sqlite3
import time

BUCKETS = 24          # twenty-four 2.5-minute buckets == the last hour
BUCKET_SECONDS = 150
MODEL_TTL = 10        # seconds; model names change at most once per turn
HISTORY_TTL = 5       # seconds; the series is cheap but the SQL is not free

DROP_PREFIXES = {'claude', 'anthropic', 'openai', 'google', 'meta', 'mistral', 'mistralai',
                 'deepseek-ai', 'x-ai', 'moonshot', 'moonshotai', 'z-ai', 'nvidia', 'microsoft'}
DATE_SUFFIX = re.compile(r'[-_](\d{8}|\d{6}|v\d+(\.\d+)?)$')
MAX_LABEL = 16        # characters the model capsule has to fit at stream size


def compact_model(name):
    """'claude-sonnet-4-5-20250929' -> 'SONNET 4.5', 'gpt-5.6-luna' -> 'GPT 5.6 LUNA'."""
    if not name:
        return None
    tokens = [t for t in re.split(r'[-_\s]+', DATE_SUFFIX.sub('', str(name).rsplit('/', 1)[-1])) if t]
    if tokens and tokens[0].lower() in DROP_PREFIXES:
        tokens = tokens[1:] or tokens
    if not tokens:
        return None
    # Rejoin a dotted version so 4-5 reads as 4.5 rather than 4 5.
    merged = []
    for token in tokens:
        if merged and token.isdigit() and merged[-1].isdigit():
            merged[-1] = f'{merged[-1]}.{token}'
        else:
            merged.append(token)
    label = merged[0]
    for token in merged[1:]:
        if len(f'{label} {token}') > MAX_LABEL:
            break
        label = f'{label} {token}'
    return label.upper()[:MAX_LABEL]


def bucket_series(events, now, buckets=BUCKETS, width=BUCKET_SECONDS):
    """Bin (timestamp, tokens) pairs oldest-first; the last bucket is 'now'."""
    series = [0] * buckets
    for stamp, tokens in events:
        age = now - stamp
        if 0 <= age < buckets * width:
            series[buckets - 1 - int(age // width)] += tokens
    return series


def codex_events(readers):
    """(timestamp, tokens) pairs from the parsed rollout readers, if present."""
    for reader in list(getattr(readers, 'values', lambda: [])()):
        for event in list(getattr(reader, 'events', []) or []):
            yield event


def claude_events(files):
    """(timestamp, tokens) pairs from the parsed Claude transcripts, if present."""
    for record in list(getattr(files, 'values', lambda: [])()):
        for stamp, tokens, _output in list((record or {}).get('messages', {}).values()):
            yield stamp, tokens


def codex_model(path):
    """First turn_context model in a rollout: cheap head scan, stable per rollout."""
    try:
        with Path(path).open() as stream:
            for index, line in enumerate(stream):
                if index > 400:
                    break
                if '"model"' not in line or 'turn_context' not in line:
                    continue
                try:
                    payload = json.loads(line).get('payload', {})
                except ValueError:
                    continue
                if payload.get('model'):
                    return payload['model']
    except OSError:
        return None
    return None


def newest_claude_model(projects):
    files = sorted(Path(projects).glob('**/*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    for path in files[:3]:
        try:
            with path.open('rb') as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 262144))
                tail = stream.read().decode('utf-8', 'ignore').splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            if '"model"' not in line:
                continue
            try:
                model = (json.loads(line).get('message') or {}).get('model')
            except ValueError:
                continue
            if model:
                return model
    return None


def newest_codex_rollout(codex_root):
    files = Path(codex_root, 'sessions').glob('**/*.jsonl')
    try:
        return max(files, key=lambda f: f.stat().st_mtime)
    except (OSError, ValueError):
        return None


def hermes_row(db_path):
    """Newest top-level session's model plus its per-model usage intervals.

    Token counts follow the definition tools.agent_telemetry.hermes_usage now
    uses (canonical total = uncached input + cache read/write + output), so the
    graph is on the same scale as the session/30-day capsules.
    """
    buckets = '+'.join('COALESCE(' + key + ',0)' for key in
                       ('input_tokens', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens'))
    try:
        with sqlite3.connect(Path(db_path).as_uri() + '?mode=ro', uri=True) as db:
            session = db.execute('SELECT model FROM sessions WHERE parent_session_id IS NULL '
                                 'AND message_count > 0 ORDER BY COALESCE(last_activity_at, started_at) '
                                 'DESC LIMIT 1').fetchone()
            usage = db.execute(f'SELECT first_seen, last_seen, {buckets} FROM session_model_usage').fetchall()
    except (sqlite3.Error, OSError):
        return None, []
    return (session[0] if session else None), usage


def spread(intervals, now, buckets=BUCKETS, width=BUCKET_SECONDS):
    """Distribute interval totals over the buckets they overlap (Hermes fallback)."""
    values = [0.0] * buckets
    start, span_end = now - buckets * width, now
    for first_seen, last_seen, tokens in intervals:
        if not tokens or first_seen is None or last_seen is None:
            continue
        first, last = float(first_seen), float(last_seen)
        if last < start or first > span_end:
            continue
        duration = max(0.0, last - first)
        for index in range(buckets):
            low, high = start + index * width, start + (index + 1) * width
            overlap = min(high, last) - max(low, first)
            if overlap <= 0:
                continue
            share = 1.0 if duration <= 0 else overlap / duration
            values[index] += tokens * share
    # Keep the bucket sum honest: rounding each bucket can quietly lose tokens.
    series = [int(round(value)) for value in values]
    residual = int(round(sum(values))) - sum(series)
    if residual and any(values):
        series[max(range(buckets), key=lambda index: values[index])] += residual
    return series


class Extras:
    """Cached model names and hourly series for the three agents."""

    def __init__(self, codex_root, claude_root, hermes_db):
        self.codex_root, self.claude_root, self.hermes_db = Path(codex_root), Path(claude_root), Path(hermes_db)
        self.models = {}
        self.models_at = 0
        self.history = {}
        self.history_at = 0

    def model(self, agent):
        with_model = self.models if time.monotonic() - self.models_at < MODEL_TTL else {}
        if not with_model:
            rollout = newest_codex_rollout(self.codex_root)
            with_model = {
                'codex': codex_model(rollout) if rollout else None,
                'claude': newest_claude_model(self.claude_root / 'projects'),
                'hermes': hermes_row(self.hermes_db)[0],
            }
            self.models, self.models_at = with_model, time.monotonic()
        return compact_model(with_model.get(agent))

    def series(self, agent, codex_readers=None, claude_files=None, now=None):
        now = now or time.time()
        if time.monotonic() - self.history_at >= HISTORY_TTL or not self.history:
            self.history = {
                'codex': bucket_series(codex_events(codex_readers), now),
                'claude': bucket_series(claude_events(claude_files), now),
                'hermes': spread(hermes_row(self.hermes_db)[1], now),
            }
            self.history_at = time.monotonic()
        return list(self.history.get(agent) or [0] * BUCKETS)

    def metrics(self, agent, codex_readers=None, claude_files=None, now=None):
        """The two extra capsules: model name and 1-hour token consumption."""
        now = now or time.time()
        series = self.series(agent, codex_readers, claude_files, now)
        total = sum(series)
        return {
            'model': {'title': 'MODEL', 'value': self.model(agent) or '—', 'detail': ''},
            'spark': {'title': '1H TOK', 'value': compact(total) if total else '0',
                      'detail': '', 'series': series},
        }


def compact(value):
    """Same compacting the telemetry modules use, so the units match the tiles."""
    if value is None:
        return '—'
    for divisor, suffix in [(1e9, 'B'), (1e6, 'M'), (1e3, 'K')]:
        if value >= divisor:
            return f'{value / divisor:.1f}{suffix}'
    return str(int(value))

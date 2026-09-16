#!/usr/bin/env python3
"""Is the tally actually silent while the chyrons are off air?

Watches the live service: samples the counters and the tally sources's play
count while the Chryons scene is off air, and reports whether any increment
made a sound. Run it with agent activity going on (a Codex/Claude/Hermes turn)
so the counters really move - a quiet sample proves nothing.

    python3 scripts/check_tally_gate.py --window 30
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

BASE = 'http://127.0.0.1:8790'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)


def counters():
    data = get('/api/telemetry')
    return ({a['key']: (a['metrics']['session']['value'], a['metrics']['month']['value'])
             for a in data['agents']}, data['visibility'])


def plays(page='audio-only'):
    """The audio source's play count - never a test browser's."""
    health = get('/api/health')
    reports = health.get('tally_reports') or {}
    report = reports.get(page) or health.get('tally') or {}
    return report.get('plays'), report.get('state')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=float, default=30, help='seconds to watch')
    args = parser.parse_args()

    before, visibility = counters()
    start_plays, state = plays()
    print(f'on_air={visibility["on_air"]} audio={state} tally plays={start_plays}')
    print('counters at start:', before)
    other = {page: report['plays'] for page, report in
             (get('/api/health').get('tally_reports') or {}).items() if page != 'audio-only'}
    if other:
        print('other pages reporting (ignored):', other)

    moved, played, deadline = set(), start_plays, time.time() + args.window
    while time.time() < deadline:
        time.sleep(2)
        now, _ = counters()
        moved |= {key for key in before if now[key] != before[key]}
        played, state = plays()
        print(f'  t+{args.window - (deadline - time.time()):>5.0f}s  moved={sorted(moved) or "none"}  plays={played}')

    print('counters at end:  ', counters()[0])
    if visibility['on_air']:
        print('INCONCLUSIVE: the Chryons scene is on air, so the tally is allowed to play')
        return 0
    if not moved:
        print('INCONCLUSIVE: no counters moved, so there was nothing to make a sound about')
        return 0
    if played != start_plays:
        print(f'FAIL: the tally played {played - start_plays}x while the scene was off air')
        return 1
    print(f'PASS: {sorted(moved)} grew and the tally stayed silent ({start_plays} plays)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

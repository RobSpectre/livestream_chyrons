#!/usr/bin/env python3
"""Replace the Crickets / Laughs / Sad keys on page 1 with chyron toggles.

Two targets, because the deck has two layers:

  --spec  the layout spec (streamdeck/opendeck-layout.json) that generates the
          profiles. This is the durable edit: `build_opendeck.py` regenerates the
          profiles from it, so a change made only in the app config is lost the
          next time the deck is installed.
  --profile  the installed profile in ~/.config/opendeck, for a live change
          without rebuilding.

The three keys become `item` controls on the Chryons scene, exactly like the
camera keys on the same page: amber while the chyron is up, dim while it is
hidden, red when OBS cannot answer. OBS still owns visibility and the reflow
service still owns position, so a toggle is all this needs to do.

    python3 scripts/wire_deck_chyrons.py --spec ../streamdeck/opendeck-layout.json --dry-run
    python3 scripts/make_deck_icons.py --controls ../streamdeck/images/controls

Idempotent: keys are matched by the settings id they replace, so a second run
reports "already wired" instead of rewriting. Writes a timestamped backup first.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
SCENE = 'Chryons'

# Replaces -> the chyron it toggles, in deck order.
WIRING = (
    ('effect:Crickets', 'codex'),
    ('effect:Laughs', 'claude'),
    ('effect:Sad', 'hermes'),
)

# Key appearance per state, taken from the keys' neighbours on page 1:
# 0 = hidden (dim slate), 1 = shown (amber), 2 = unknown (red).
STATES = (
    {'background': '#10141b', 'colour': '#b8c4d4'},
    {'background': '#201604', 'colour': '#ffe7a3'},
    {'background': '#10141b', 'colour': '#b8c4d4'},
)


def icons(root):
    """{agent: [icon for each state]} from the icon generator."""
    spec = importlib.util.spec_from_file_location('make_deck_icons', ROOT / 'scripts/make_deck_icons.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tiles = module.tiles(root)
    order = ('hidden', 'shown', 'unknown')
    return {agent: [f'images/hackparty/{tiles[agent][state][0]}' for state in order]
            for agent in tiles}


def rewrite(profile, image_root, dry_run=False):
    """Point the three keys at their chyron. Returns (changed, notes)."""
    icons_by_agent = icons(image_root)
    keys = profile.get('keys') or []
    # Match either the sound-effect id being replaced or the chyron id we wrote.
    targets = {}
    for legacy, agent in WIRING:
        targets[legacy] = agent
        targets[f'chyron:{agent}'] = agent
    changed, notes = [], []

    for key in keys:
        settings = key.get('settings') or {}
        settings_id = settings.get('id')
        if settings_id not in targets:
            continue
        agent = targets[settings_id]
        label = agent.title()
        if settings.get('kind') == 'item' and settings.get('scene') == SCENE:
            notes.append(f'{key["context"]}: already toggles {settings.get("source")}')
            continue

        source = f'Token Chyron - {label}'
        missing = [image for image in icons_by_agent[agent]
                   if not (image_root / Path(image).name).is_file()]
        if missing:
            raise SystemExit(f'missing icons for {agent}: {missing} '
                             '- run scripts/make_deck_icons.py first')

        labels = [label, label, label]
        key['settings'] = {
            '_label_defaults': labels,
            '_label_id': settings.get('_label_id', ''),
            '_label_mode': 'shared',
            'id': f'chyron:{agent}',
            'kind': 'item',
            'scene': SCENE,
            'source': source,
        }
        states = key.get('states') or []
        if len(states) != 3:
            raise SystemExit(f'{key["context"]}: expected 3 states, found {len(states)}')
        for index, state in enumerate(states):
            state['text'] = label
            state['background_colour'] = STATES[index]['background']
            state['colour'] = STATES[index]['colour']
            state['image'] = icons_by_agent[agent][index]
        key['current_state'] = 0
        changed.append(f'{key["context"]}: {settings_id} -> item on {SCENE}/{source}')
        notes.append(f'{key["context"]}: {label}')

    found = {note.split(":")[0] for note in notes}
    for context in [f'Keypad.{index}.0' for index in (27, 28, 29)]:
        if context not in found:
            notes.append(f'{context}: no key found (nothing to do)')
    return changed, notes


def rewrite_spec(spec, serial='CL37L2A01125', page='0', dry_run=False):
    """Point the three page-1 buttons in the layout spec at their chyron.

    The spec is what the deck profiles are generated from, so this is the
    durable edit. Mirrors the shape of the camera keys: one base state, a
    live_control of kind `item`, and one status icon per state.
    """
    buttons = spec['devices'][serial]['buttons'][page]
    changed, notes = [], []
    for position in sorted(buttons, key=int):
        live = buttons[position].get('live_control') or {}
        if live.get('id') not in dict(WIRING):
            continue
        if live.get('kind') == 'item' and live.get('scene') == SCENE:
            notes.append(f'Keypad.{position}.0: already toggles {live.get("source")}')
            continue
        agent = dict(WIRING)[live['id']]
        label = agent.title()
        button = buttons[position]
        icon = f'images/controls/chyron-{agent}-off.svg'
        base = dict((button.get('states') or {'0': {}}).get('0') or {})
        base['text'] = label
        base['icon'] = icon
        button['states'] = {'0': base}
        button['state'] = 0
        button['live_control'] = {'kind': 'item', 'scene': SCENE,
                                  'source': f'Token Chyron - {label}',
                                  'id': f'chyron:{agent}'}
        button['status_icons'] = [f'images/controls/chyron-{agent}-{state}.svg'
                                  for state in ('off', 'on', 'unknown')]
        changed.append(f'Keypad.{position}.0: {live["id"]} -> item on {SCENE}/{label}')
        notes.append(f'Keypad.{position}.0: {label}')
    return changed, notes


def rewrite_document(path, document, **kwargs):
    """Write it back the way the file was formatted, so the diff shows the edit.

    Guessing an indent turns a three-button change into a whole-file rewrite in
    version control, which hides the real edit from review.
    """
    width = 2
    for line in path.read_text().splitlines()[1:]:
        if line.startswith(' '):
            width = len(line) - len(line.lstrip(' '))
            break
    return json.dumps(document, indent=width, sort_keys=False) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',
                        default=str(Path.home() / '.config/opendeck/profiles/sd-CL37L2A01125/01 - broadcast.json'))
    parser.add_argument('--spec', help='edit the deck layout spec instead of an installed profile')
    parser.add_argument('--images', default=str(Path.home() / '.config/opendeck/images/hackparty'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    path = Path(args.spec or args.profile)
    document = json.loads(path.read_text())
    if args.spec:
        changed, notes = rewrite_spec(document, dry_run=args.dry_run)
    else:
        changed, notes = rewrite(document, Path(args.images), dry_run=args.dry_run)
    for note in notes:
        print(' ', note)
    if not changed:
        print('already wired - nothing written')
        return 0
    for line in changed:
        print(' ', ('would change ' if args.dry_run else 'changed '), line, sep='')
    if not args.dry_run:
        backup = path.with_suffix(f'.json.bak-{time.strftime("%Y%m%d-%H%M%S")}')
        shutil.copy2(path, backup)
        path.write_text(rewrite_document(path, document))
        print(f'  backup: {backup}')
        if args.spec:
            print('  next: python3 tools/build_opendeck.py   (in the streamdeck repo)')
        else:
            print('  restart OpenDeck (or switch profile and back) to load the new keys')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

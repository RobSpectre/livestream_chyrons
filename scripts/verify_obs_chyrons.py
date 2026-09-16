#!/usr/bin/env python3
"""Verify the token chyron wiring in OBS, including the top-down reflow.

Checks the Chryons scene, one browser source per agent, the chroma key, the
nesting that keeps them on the stream and the audio-only tally source - then
hides a chyron and measures how fast and how far the rest reflow - and finally
screenshots the scene and writes 1080p/720p previews. Writes PNGs to --out
(default /tmp).

    STREAMDECK_ROOT=/media/rspectre/Storage/workspace/streamdeck \
    /media/rspectre/Storage/workspace/streamdeck/.venv/bin/python scripts/verify_obs_chyrons.py
"""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from obs_chyrons import (COLLECTION, LAYER_SCENE, ORDER, SCENE, SOURCE_HEIGHT, SOURCE_WIDTH,  # noqa: E402
                         connect, nesting_targets, scene_items, scene_names, source_name)
from obs_reflow import TALLY_SOURCE, row_layout  # noqa: E402

results = []


def check(name, passed, detail=''):
    results.append((name, passed))
    print(f'{"PASS" if passed else "FAIL"}  {name}{"  -- " + detail if detail else ""}')


def positions(client):
    return {i['sourceName']: round(i['sceneItemTransform']['positionY'])
            for i in client.get_scene_item_list(SCENE).scene_items}


def _opaque_share(path):
    """Fraction of a screenshot that is not transparent: 0 means nothing rendered."""
    try:
        from PIL import Image
    except ImportError:
        return 1.0
    with Image.open(path).convert('RGBA') as image:
        raw = image.tobytes()
    alpha = raw[3::4]
    return sum(1 for value in alpha if value > 8) / max(1, len(alpha))


def settle(client, wanted, timeout=3.0):
    """Wait for the reflow to reach `wanted`, returning (seconds, last seen)."""
    started = time.monotonic()
    seen = positions(client)
    while time.monotonic() - started < timeout:
        seen = positions(client)
        if seen == wanted:
            return time.monotonic() - started, seen
        time.sleep(0.05)
    return None, seen


def main():
    started_at = time.time()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='/tmp')
    args = parser.parse_args()
    out = Path(args.out)

    with connect() as client:
        collection = client.get_scene_collection_list().current_scene_collection_name
        check('scene collection is hack.party', collection == COLLECTION, collection)
        width, height = client.get_video_settings().base_width, client.get_video_settings().base_height
        check('the Chryons scene exists', SCENE in scene_names(client), SCENE)

        inputs = {i['inputName']: i for i in client.get_input_list().inputs}
        items = scene_items(client, SCENE)
        # Which chyrons are up right now is the operator's business (the deck keys
        # toggle them), so the expected layout follows the current visibility
        # instead of assuming all three are showing.
        visible = [a for a in ORDER if items.get(source_name(a)) and items[source_name(a)]['sceneItemEnabled']]
        layout = row_layout(visible)
        print(f'NOTE  chyrons currently visible: {", ".join(visible) or "none"}')
        for agent in ORDER:
            name = source_name(agent)
            source = inputs.get(name)
            check(f'{name} exists as a browser source',
                  bool(source) and source.get('inputKind') == 'browser_source',
                  str(source and source.get('inputKind')))
            item = items.get(name)
            check(f'{name} lives in {SCENE}, not {LAYER_SCENE}',
                  bool(item) and name not in scene_items(client, LAYER_SCENE),
                  f'enabled={item and item["sceneItemEnabled"]}')
            if not item:
                continue
            transform = client.get_scene_item_transform(SCENE, item['sceneItemId']).scene_item_transform
            settings = client.get_input_settings(name).input_settings
            filters = {f['filterName']: f for f in client.get_source_filter_list(name).filters}
            check(f'{name} points at the chyron server with its own agent',
                  settings.get('url', '').endswith(f'?agent={agent}&bg=green'), settings.get('url', ''))
            check(f'{name} renders at the 4K banner size',
                  (settings.get('width'), settings.get('height')) == (SOURCE_WIDTH, SOURCE_HEIGHT),
                  f'{settings.get("width")}x{settings.get("height")}')
            check(f'{name} keeps running while hidden', settings.get('shutdown') is False,
                  str(settings.get('shutdown')))
            check(f'{name} spans the canvas width',
                  round(SOURCE_WIDTH * transform['scaleX']) == width and transform['positionX'] == 0,
                  f'{round(SOURCE_WIDTH * transform["scaleX"])}px at scale {transform["scaleX"]}')
            check(f'{name} sits in its top-down row',
                  abs(transform['positionY'] - layout[name]) <= 0.5,
                  f'y={transform["positionY"]:.0f}, expected {layout[name]}')
            key = filters.get('Chroma Key')
            check(f'{name} has the chroma key filter', bool(key), str(list(filters)))
            if key:
                kind = client.get_source_filter(name, 'Chroma Key').filter_kind
                tuned = client.get_source_filter(name, 'Chroma Key').filter_settings
                check(f'{name} keys on green', kind.startswith('chroma_key_filter') and
                      tuned.get('key_color_type') == 'green', f'{kind} {json.dumps(tuned)}')

        # Nesting keeps the chyrons on the stream the way Scene Overlays used to.
        targets = nesting_targets(client)
        check('the Chryons scene is nested into the livestream scenes',
              len(targets) >= 10 and all(SCENE in scene_items(client, t) for t in targets),
              f'{len(targets)} scenes')
        in_front = []
        for target in targets:
            names = [i['sourceName'] for i in client.get_scene_item_list(target).scene_items]
            in_front.append(names.index(SCENE) <= names.index(LAYER_SCENE))
        check('Chryons sits in front of Scene Overlays wherever it is nested',
              all(in_front), f'{sum(in_front)}/{len(in_front)} scenes')

        # The audio-only tally source.
        tally = inputs.get(TALLY_SOURCE)
        check('the tally source exists as a browser source',
              bool(tally) and tally.get('inputKind') == 'browser_source',
              str(tally and tally.get('inputKind')))
        if tally:
            settings_t = client.get_input_settings(TALLY_SOURCE).input_settings
            volume = client.get_input_volume(TALLY_SOURCE).input_volume_mul
            muted = client.get_input_mute(TALLY_SOURCE).input_muted
            tracks = client.get_input_audio_tracks(TALLY_SOURCE).input_audio_tracks
            item_t = items.get(TALLY_SOURCE)
            check('the tally source renders nothing visible',
                  settings_t.get('url', '').endswith('?tally=1') and item_t is not None and
                  item_t['sceneItemEnabled'],
                  f'{settings_t.get("url")} enabled={item_t and item_t["sceneItemEnabled"]}')
            check('the tally source is audible but not deafening',
                  not muted and 0 < volume <= 1 and tracks.get('1') is True,
                  f'volume={volume:.2f} muted={muted} track1={tracks.get("1")}')
            transform_t = client.get_scene_item_transform(SCENE, item_t['sceneItemId']).scene_item_transform
            check('the tally source is parked off the canvas',
                  transform_t['positionX'] < 0 and transform_t['positionY'] < 0,
                  f'({transform_t["positionX"]:.0f},{transform_t["positionY"]:.0f})')

        # Reflow: hide the top visible chyron, watch the rest rise, then put it back.
        if len(visible) >= 2:
            top_agent = visible[0]
            top_item = items.get(source_name(top_agent))
            before = positions(client)
            # Overlay the risen layout on the full position map: settle() compares
            # every scene item, including the tally source that stays parked.
            risen = dict(before, **row_layout(list(visible[1:])))
            client.set_scene_item_enabled(SCENE, top_item['sceneItemId'], False)
            took, seen = settle(client, risen)
            check(f'hiding the top chyron ({top_agent}) lifts the others into the top rows',
                  took is not None,
                  json.dumps({k.rsplit(" ", 1)[-1]: v for k, v in seen.items()}))
            check('the reflow reacts promptly', took is not None and took < 1.0,
                  f'{took * 1000:.0f}ms' if took else f'never settled: {json.dumps(seen)}')
            client.set_scene_item_enabled(SCENE, top_item['sceneItemId'], True)
            took_back, seen_back = settle(client, before)
            check('showing it again restores the stack', took_back is not None,
                  f'{took_back * 1000:.0f}ms' if took_back else json.dumps(seen_back))
        else:
            print(f'NOTE  reflow toggle test skipped: {len(visible)} chyron(s) visible, '
                  'the test needs at least two')

        # Is the Chryons scene actually on air right now? That depends on which
        # scene the operator has selected, so it is reported, not asserted.
        program = client.get_scene_list().current_program_scene_name
        active = client.get_source_active(SCENE)
        on_air = bool(active.video_showing)
        print(f'NOTE  program scene is {program!r}; Chryons on air: {on_air}')
        if not on_air:
            print(f'NOTE  screenshots skipped: OBS only renders sources in the program path, '
                  f'and {SCENE!r} is not in {program!r}')

        if on_air:
            # Screenshots: proof the browser sources really loaded and key out.
            # A chyron that is toggled off is parked off-canvas, so there is
            # nothing to capture for it.
            shots = [(SCENE, 'obs-chryons.png')]
            shots += [(source_name(a), f'obs-chyron-{a}.png') for a in visible]
            for target, filename in shots:
                try:
                    shot = client.get_source_screenshot(target, 'png', 1920, 1080, quality=100)
                except Exception as error:  # noqa: BLE001 - reported as a failed check
                    check(f'screenshot {target}', False, str(error))
                    continue
                data = shot.image_data.split(',', 1)[-1]
                path = out / filename
                path.write_bytes(base64.b64decode(data))
                opaque = _opaque_share(path)
                check(f'screenshot {target}', opaque > 0.01, f'{path} ({opaque:.0%} opaque)')

    # What the encoder sees: the keyed overlay layer over a mid-grey picture (a
    # black frame and black shadow only read against something lighter), at
    # canvas resolution and again at 720p.
    capture = out / 'obs-chryons.png'
    if not capture.is_file() or capture.stat().st_mtime < started_at:
        print('NOTE  no fresh scene capture, skipping the 1080p/720p previews')
    else:
        try:
            from PIL import Image
        except ImportError:
            check('1080p/720p stream previews', True, 'Pillow unavailable, skipped')
        else:
            layer = Image.open(capture).convert('RGBA')
            frame = Image.new('RGBA', layer.size, (200, 200, 200, 255))
            frame.alpha_composite(layer)
            frame.convert('RGB').save(out / 'chyron-stream-1080p.png')
            frame.convert('RGB').resize((1280, 720), Image.LANCZOS).save(out / 'chyron-stream-720p.png')
            check('1080p/720p stream previews', True,
                  f'{out}/chyron-stream-1080p.png and {out}/chyron-stream-720p.png')

    failed = [name for name, passed in results if not passed]
    print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())

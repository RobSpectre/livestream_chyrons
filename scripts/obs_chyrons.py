#!/usr/bin/env python3
"""Create the token chyrons in OBS: sources, scene, and where they show up.

Layout, all idempotent:

  * three 4K browser sources - ``Token Chyron - Codex/Claude/Hermes`` - plus the
    audio-only ``Token Chyron - Tally``, all inside a dedicated **Chryons**
    scene instead of sharing Scene Overlays;
  * inside Chryons they sit top-down as rows, and the chyron server keeps them
    stacked from the top as you show and hide them (see server/obs_reflow.py);
  * the Chryons scene is nested into every scene that used to contain Scene
    Overlays, directly in front of it, so nothing disappears from the stream.

Because OBS cannot rename inputs, a migration from the older ``Chyron - *``
names creates the new sources and removes the old ones.

    .venv/bin/python scripts/obs_chyrons.py --list     # show the current state
    .venv/bin/python scripts/obs_chyrons.py            # create/update/migrate
    .venv/bin/python scripts/obs_chyrons.py --remove   # take the chyrons back out

Connects the same way the OpenDeck plugin does (control_obs_background.connect),
so it uses OBS's own websocket config and needs OBS running with the hack.party
collection selected.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STREAMDECK = Path(os.environ.get('STREAMDECK_ROOT', '/media/rspectre/Storage/workspace/streamdeck'))

sys.path.insert(0, str(ROOT / 'server'))
from obs_reflow import (COLLECTION, LAYER_SCENE, ORDER, SCENE, SOURCE_NAMES,  # noqa: E402
                        TALLY_SOURCE, row_layout)

BASE_URL = 'http://127.0.0.1:8790/'
SOURCE_WIDTH = 3840                       # native 4K banner width (see tests/overlay-harness.mjs)
SOURCE_HEIGHT = 440                       # banner 391px + padding + room for its 16px shadow
TALLY_SIZE = 64                           # the audio-only source draws nothing
TALLY_VOLUME = 0.6                        # OBS source volume, on top of ?volume=
LEGACY = [f'Chyron - {name}' for name in ('Codex', 'Claude', 'Hermes', 'Tally')]


def source_name(agent):
    return SOURCE_NAMES[agent]


def url_for(agent, chroma):
    return f'{BASE_URL}?agent={agent}&bg={chroma}'


def connect():
    if not (STREAMDECK / 'control_obs_background.py').is_file():
        raise SystemExit(f'Stream Deck tooling not found at {STREAMDECK} (set STREAMDECK_ROOT)')
    sys.path.insert(0, str(STREAMDECK))
    from control_obs_background import connect as obs_connect
    return obs_connect()


def chroma_kind(client):
    """Newest chroma key filter this OBS build offers."""
    kinds = [k for k in client.get_source_filter_kind_list().source_filter_kinds
             if k.startswith('chroma_key_filter')]
    if not kinds:
        raise SystemExit('This OBS build has no chroma key filter')
    return sorted(kinds)[-1]


def desired_filters(client):
    kind = chroma_kind(client)
    defaults = client.get_source_filter_default_settings(kind).default_filter_settings
    # Start from OBS's own defaults and change only the key tuning.
    return kind, dict(defaults, key_color_type='green', similarity=400, smoothness=80, spill=100)


def canvas(client):
    video = client.get_video_settings()
    return video.base_width, video.base_height


def scale_for(client):
    width, _ = canvas(client)
    return width / SOURCE_WIDTH


def existing(client):
    return {i['inputName']: i for i in client.get_input_list().inputs}


def scene_names(client):
    return [s['sceneName'] for s in client.get_scene_list().scenes]


def scene_items(client, scene):
    return {i['sourceName']: i for i in client.get_scene_item_list(scene).scene_items}


def nesting_targets(client):
    """Scenes that used to show the chyrons through Scene Overlays."""
    return [name for name in scene_names(client)
            if name != SCENE and LAYER_SCENE in scene_items(client, name)]


def ensure_scene(client, scene):
    if scene not in scene_names(client):
        client.create_scene(scene)
        print(f'created scene     : {scene}')


def ensure_banner(client, agent, chroma, filters):
    name = source_name(agent)
    settings = {'url': url_for(agent, chroma), 'width': SOURCE_WIDTH, 'height': SOURCE_HEIGHT,
                'shutdown': False, 'restart_when_active': False}
    if name in existing(client):
        client.set_input_settings(name, settings, overlay=True)
    else:
        client.create_input(SCENE, name, 'browser_source', settings, sceneItemEnabled=True)
    item = scene_items(client, SCENE).get(name)
    if item is None:
        raise RuntimeError(f'{name} was created but did not appear in {SCENE}')
    client.set_scene_item_enabled(SCENE, item['sceneItemId'], True)
    kind, tuning = filters
    if 'Chroma Key' not in {f['filterName'] for f in client.get_source_filter_list(name).filters}:
        client.create_source_filter(name, 'Chroma Key', kind, tuning)
    return item


def ensure_tally(client):
    settings = {'url': f'{BASE_URL}?tally=1', 'width': TALLY_SIZE, 'height': TALLY_SIZE,
                'shutdown': False, 'restart_when_active': False}
    if TALLY_SOURCE in existing(client):
        client.set_input_settings(TALLY_SOURCE, settings, overlay=True)
    else:
        client.create_input(SCENE, TALLY_SOURCE, 'browser_source', settings, sceneItemEnabled=True)
    item = scene_items(client, SCENE).get(TALLY_SOURCE)
    if item is None:
        raise RuntimeError(f'{TALLY_SOURCE} was created but did not appear in {SCENE}')
    # Off-canvas: the page paints nothing, it only plays the tally.
    client.set_scene_item_transform(SCENE, item['sceneItemId'],
                                    {'positionX': -TALLY_SIZE - 8, 'positionY': -TALLY_SIZE - 8})
    client.set_scene_item_enabled(SCENE, item['sceneItemId'], True)
    client.set_input_volume(TALLY_SOURCE, TALLY_VOLUME)
    client.set_input_audio_tracks(TALLY_SOURCE, {1: True})
    return item


def place_rows(client, visible=None):
    """Top-down rows for whichever chyrons are visible, at canvas scale."""
    layout = row_layout(list(visible if visible is not None else ORDER))
    scale = scale_for(client)
    items = scene_items(client, SCENE)
    for agent in ORDER:
        item = items.get(source_name(agent))
        if item:
            client.set_scene_item_transform(
                SCENE, item['sceneItemId'],
                {'positionX': 0.0, 'positionY': float(layout[item['sourceName']]),
                 'scaleX': scale, 'scaleY': scale})
    return layout


def nest_into_scenes(client):
    """Put the Chryons scene in front of Scene Overlays wherever that was used."""
    added = []
    for target in nesting_targets(client):
        items = client.get_scene_item_list(target).scene_items
        names = [i['sourceName'] for i in items]
        if SCENE in names:
            continue
        created = client.create_scene_item(target, SCENE)
        # get_scene_item_list is front-first, so matching Scene Overlays' index
        # puts Chryons directly in front of the overlays it used to live in.
        client.set_scene_item_index(target, created.scene_item_id, names.index(LAYER_SCENE))
        client.set_scene_item_enabled(target, created.scene_item_id, True)
        added.append(target)
    return added


def remove_legacy(client):
    """OBS cannot rename an input, so the old names are created fresh and dropped."""
    gone = []
    for scene in scene_names(client):
        items = scene_items(client, scene)
        for name in LEGACY:
            if name in items:
                client.remove_scene_item(scene, items[name]['sceneItemId'])
                gone.append(f'{name} from {scene}')
    for name in LEGACY:
        if name in existing(client):
            client.remove_input(name)
            gone.append(f'input {name}')
    return gone


def apply(client, chroma):
    if client.get_scene_collection_list().current_scene_collection_name != COLLECTION:
        raise SystemExit(f'Select the {COLLECTION} scene collection in OBS first.')
    filters = desired_filters(client)
    ensure_scene(client, SCENE)
    for agent in ORDER:
        ensure_banner(client, agent, chroma, filters)
    ensure_tally(client)
    layout = place_rows(client)
    nested = nest_into_scenes(client)
    legacy = remove_legacy(client)
    return filters[0], layout, nested, legacy


def report(client):
    width, height = canvas(client)
    print(f'collection : {client.get_scene_collection_list().current_scene_collection_name}')
    print(f'canvas     : {width}x{height}   scale {scale_for(client):.3f}')
    print(f'scene      : {SCENE}')
    items = scene_items(client, SCENE)
    for agent in ORDER:
        name = source_name(agent)
        item = items.get(name)
        if not item:
            print(f'  {name:<26} MISSING')
            continue
        transform = client.get_scene_item_transform(SCENE, item['sceneItemId']).scene_item_transform
        settings = client.get_input_settings(name).input_settings
        filters = [f['filterName'] for f in client.get_source_filter_list(name).filters]
        print(f'  {name:<26} shown={str(item["sceneItemEnabled"]):<5} '
              f'pos=({transform["positionX"]:.0f},{transform["positionY"]:.0f}) '
              f'scale={transform["scaleX"]:.3f} size={settings.get("width")}x{settings.get("height")} '
              f'filters={filters}')
    tally = items.get(TALLY_SOURCE)
    if tally:
        volume = client.get_input_volume(TALLY_SOURCE).input_volume_mul
        muted = client.get_input_mute(TALLY_SOURCE).input_muted
        print(f'  {TALLY_SOURCE:<26} shown={str(tally["sceneItemEnabled"]):<5} '
              f'volume={volume:.2f} muted={muted} audio-only, off-canvas')
    print(f'nested in  : {", ".join(nesting_targets(client)) or "nowhere yet"}')
    leftovers = sorted({name for scene in scene_names(client)
                        for name in scene_items(client, scene) if name in LEGACY})
    print(f'legacy     : {", ".join(leftovers) if leftovers else "none"}')


def refresh(client, agents=ORDER):
    """Force OBS's embedded browser to drop the cached page (CEF survives a
    settings write, so a rebuilt overlay otherwise keeps rendering the old
    bundle until the source is refreshed)."""
    done = []
    for name in [source_name(agent) for agent in agents] + [TALLY_SOURCE]:
        try:
            client.press_input_properties_button(name, 'refreshnocache')
            done.append(name)
        except Exception as error:  # noqa: BLE001 - older builds lack the button
            print(f'could not refresh {name}: {type(error).__name__}: {error}')
    return done


def remove(client):
    for target in nesting_targets(client):
        items = scene_items(client, target)
        if SCENE in items:
            client.remove_scene_item(target, items[SCENE]['sceneItemId'])
            print(f'removed scene item: {SCENE} from {target}')
    items = scene_items(client, SCENE)
    for name in [source_name(a) for a in ORDER] + [TALLY_SOURCE]:
        if name in items:
            client.remove_scene_item(SCENE, items[name]['sceneItemId'])
            print(f'removed scene item: {name}')
        if name in existing(client):
            client.remove_input(name)
            print(f'removed source    : {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--chroma', default='green', help='key colour for the overlay background')
    parser.add_argument('--list', action='store_true', help='show the current state and exit')
    parser.add_argument('--remove', action='store_true', help='remove the chyron sources')
    parser.add_argument('--refresh', action='store_true',
                        help='reload the pages in OBS (run after every npm run build)')
    args = parser.parse_args()
    with (STREAMDECK / '.obs-background.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # same lock the deck plugin uses for OBS writes
        with connect() as client:
            if args.remove:
                remove(client)
            elif args.list:
                pass
            elif args.refresh:
                refreshed = refresh(client)
                print(f'refreshed: {", ".join(refreshed) if refreshed else "none"}')
            else:
                kind, layout, nested, legacy = apply(client, args.chroma)
                print(f'filter: {kind}')
                print(json.dumps(layout, indent=2))
                if nested:
                    print(f'nested Chryons into: {", ".join(nested)}')
                if legacy:
                    print(f'removed legacy     : {", ".join(legacy)}')
                print(f'refreshed: {", ".join(refresh(client)) or "none"}')
            report(client)


if __name__ == '__main__':
    main()

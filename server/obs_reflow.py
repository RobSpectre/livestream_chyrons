"""Keep the token chyron sources stacked from the top of the Chryons scene.

OBS owns visibility: you toggle a chyron with the eye icon, a deck key, or the
websocket API. This module owns position. Whenever visibility changes, the
visible chyrons are laid out top-down with no gaps, and hidden ones are parked
off-canvas so their browser sources keep running and stay warm for the next
reveal.

Nothing here is agent-specific: it reads the scene, compares the layout it wants
with the transform each item already has, and writes only what differs.
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import threading
import time

SCENE = 'Chryons'
COLLECTION = 'hack.party'
LAYER_SCENE = 'Scene Overlays'      # the scene the chyrons used to live in
ORDER = ('codex', 'claude', 'hermes')
SOURCE_NAMES = {agent: f'Token Chyron - {agent.title()}' for agent in ORDER}
TALLY_SOURCE = 'Token Chyron - Tally'
TOP = 40                            # canvas pixels from the top edge
ROW = 220                           # canvas height of one chyron source
GAP = 8                             # canvas pixels between stacked chyrons
PARK_Y = -400                       # off-canvas resting place for hidden chyrons
RECONCILE_SECONDS = 2.0
RETRY_SECONDS = 5.0                 # wait between reconnect attempts


def _safe_log(*parts):
    """Default logger: a log line must never be able to stop the layout.

    A server started from a terminal that has since closed writes to a pty with
    no reader, so ``print`` raises BrokenPipeError. That exception used to come
    out of the move-logging call, escape ``run()`` and kill the reflow thread
    outright - after which a chyron switched back on stayed parked off-canvas at
    y=-400 and never appeared on stream, while ``/api/health`` froze on
    ``state: error``. Logging is diagnostics: it degrades, it never fails.
    """
    try:
        print(*parts, flush=True)
    except OSError:  # BrokenPipeError, EIO on a dead pty, ...
        pass


def row_layout(visible, top=TOP, row=ROW, gap=GAP, park=PARK_Y):
    """Pure: y position per source. Visible ones stack from the top in ORDER.

    >>> row_layout(['codex', 'hermes'])
    {'Token Chyron - Codex': 40, 'Token Chyron - Claude': -400, 'Token Chyron - Hermes': 268}
    """
    wanted = set(visible)
    positions, index = {}, 0
    for agent in ORDER:
        if agent in wanted:
            positions[SOURCE_NAMES[agent]] = top + index * (row + gap)
            index += 1
        else:
            positions[SOURCE_NAMES[agent]] = park
    return positions


def websocket_config(env=None, home=None):
    """Same config the deck plugin authenticates with."""
    env = env or __import__('os').environ
    base = Path(env.get('XDG_CONFIG_HOME', (home or Path.home()) / '.config')) / 'obs-studio'
    path = base / 'plugin_config/obs-websocket/config.json'
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return {'host': '127.0.0.1', 'port': config.get('server_port', 4455),
            'password': config.get('server_password', ''), 'timeout': 5}


def _req_client(config):
    import obsws_python as obs
    return obs.ReqClient(**config)


def visibility_snapshot(config, scene=SCENE, client_factory=None):
    """Read on-air and per-chyron visibility from OBS in one shot.

    The tally's audio gate needs to know what is on screen. When the layout is
    owned somewhere else - an OBS macro does the positioning, or this file's
    Reflow is between reconnects - nothing in this process is subscribed to OBS
    events, so ask OBS directly instead. Unknown means audible: a missing config,
    a closed OBS or a dead socket returns the permissive default rather than
    silencing the stream by accident.
    """
    permissive = {'on_air': True, 'agents': {agent: True for agent in ORDER}}
    if not config:
        return permissive
    try:
        factory = client_factory or _req_client
        with factory(config) as client:
            items = client.get_scene_item_list(scene).scene_items
            agents = {agent: any(i['sourceName'] == name and i['sceneItemEnabled']
                                 for i in items)
                      for agent, name in SOURCE_NAMES.items()}
            try:
                on_air = bool(client.get_source_active(scene).video_showing)
            except Exception:  # noqa: BLE001 - older OBS, or the scene was renamed
                on_air = True
            return {'on_air': on_air, 'agents': agents}
    except Exception:  # noqa: BLE001 - OBS closed, websocket down, auth changed
        return permissive


class Reflow:
    """Event-driven, with a slow reconcile as a safety net for missed events."""

    def __init__(self, scene=SCENE, lock_path=None, log=_safe_log, retry_seconds=RETRY_SECONDS):
        self.scene = scene
        self.lock_path = lock_path
        self.config = None
        self.log = log
        self.retry_seconds = retry_seconds
        self.state = 'off'
        self.error = None
        self.positions = {}
        self.moves = 0
        self.wake = threading.Event()
        self.stop = threading.Event()
        # What the audio side needs to know: is the scene on air, and which
        # chyrons are showing. Defaults to "everything audible" until OBS says
        # otherwise, so a missing answer never silences the tally.
        self.visibility = {'on_air': True, 'agents': {agent: True for agent in ORDER}}

    # -- layout ---------------------------------------------------------------
    def wanted(self, items):
        """Scene items -> the layout they should have."""
        visible = [agent for agent, name in SOURCE_NAMES.items()
                   if any(i['sourceName'] == name and i['sceneItemEnabled'] for i in items)]
        return row_layout(visible)

    def plan(self, items):
        """Only the moves that actually differ, so we do not fight OBS."""
        wanted = self.wanted(items)
        plan = {}
        for item in items:
            name = item['sourceName']
            if name not in wanted:
                continue
            transform = item.get('sceneItemTransform') or {}
            if abs(float(transform.get('positionY', 0)) - wanted[name]) > 0.5:
                plan[name] = (item['sceneItemId'], wanted[name])
        return plan

    # -- runtime --------------------------------------------------------------
    def start(self, config):
        self.config = config
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def _log(self, *parts):
        """Route every log line through here: a logger must never break layout.

        ``_safe_log`` covers the default print-to-a-dead-pty case; this covers a
        logger someone injects, so neither can take the thread down.
        """
        try:
            self.log(*parts)
        except Exception:  # noqa: BLE001 - diagnostics, never fatal
            pass

    def run(self):
        while not self.stop.is_set():
            try:
                self.serve()
            except Exception as error:  # noqa: BLE001 - reported through status()
                self.state = 'error'
                self.error = f'{type(error).__name__}: {error}'
                self._log(f'chyron reflow: {self.error}')
                self.stop.wait(self.retry_seconds)

    def serve(self):
        import obsws_python as obs
        from obsws_python import Subs

        with obs.ReqClient(**self.config) as client:
            events = obs.EventClient(**self.config, subs=Subs.SCENEITEMS | Subs.SCENES)
            events.callback.register(self.on_scene_item_enable_state_changed)
            events.callback.register(self.on_scene_item_list_reindexed)
            events.callback.register(self.on_scene_item_created)
            events.callback.register(self.on_current_program_scene_changed)
            try:
                self.state = 'running'
                self.error = None
                self._log('chyron reflow: connected to OBS')
                while not self.stop.is_set():
                    self.apply(client)
                    # Sleep until an event wakes us, or until the reconcile tick.
                    self.wake.wait(RECONCILE_SECONDS)
                    self.wake.clear()
            finally:
                try:
                    events.disconnect()
                except Exception:  # noqa: BLE001 - a dead socket still has to reconnect
                    pass

    def on_air(self, client):
        """Is the Chryons scene rendered right now? Unknown means "yes"."""
        try:
            return bool(client.get_source_active(self.scene).video_showing)
        except Exception:  # noqa: BLE001 - older OBS, or the scene was renamed
            return True

    def apply(self, client):
        items = client.get_scene_item_list(self.scene).scene_items
        # Visibility first, and on every pass: the tally audio reads it, and it
        # has to be right even when the layout itself needs no changes.
        self.positions = {i['sourceName']: round(float(i['sceneItemTransform']['positionY']))
                          for i in items if i.get('sceneItemTransform')}
        showing = {agent: any(i['sourceName'] == name and i['sceneItemEnabled'] for i in items)
                   for agent, name in SOURCE_NAMES.items()}
        self.visibility = {'on_air': self.on_air(client), 'agents': showing}
        plan = self.plan(items)
        if not plan:
            return 0
        with self.scene_lock():
            for name, (item_id, position) in plan.items():
                client.set_scene_item_transform(self.scene, item_id,
                                                {'positionX': 0.0, 'positionY': float(position)})
        self.moves += len(plan)
        moved = []
        for name, (_, position) in plan.items():
            short = name.rsplit(' ', 1)[-1]
            moved.append(f'{short}->{int(position)}')
        self._log('chyron reflow: ' + ' '.join(moved))
        return len(plan)

    def scene_lock(self):
        """Reuse the lock the deck plugin takes around its OBS writes."""
        if not self.lock_path:
            return _NullLock()
        return _FileLock(Path(self.lock_path))

    # -- events ---------------------------------------------------------------
    def on_scene_item_enable_state_changed(self, data):
        if getattr(data, 'scene_name', None) == self.scene:
            self.wake.set()

    def on_scene_item_list_reindexed(self, data):
        if getattr(data, 'scene_name', None) == self.scene:
            self.wake.set()

    def on_scene_item_created(self, data):
        if getattr(data, 'scene_name', None) == self.scene:
            self.wake.set()

    def on_current_program_scene_changed(self, data):
        # Switching scenes changes whether the chyrons (and therefore the tally
        # audio) are on air.
        self.wake.set()

    def status(self, positions=None):
        return {'state': self.state, 'scene': self.scene, 'moves': self.moves,
                'positions': positions or self.positions, 'visibility': self.visibility,
                'error': self.error}


class _FileLock:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open('w')
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exception):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

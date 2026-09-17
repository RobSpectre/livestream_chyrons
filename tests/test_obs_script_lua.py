"""Run the generated OBS Lua script for real, against a stub of OBS's API.

`tests/test_obs_script.py` checks the numbers the generator emits; this checks
the file OBS will actually load: it executes obs/chyrons-reflow.lua with a fake
`obslua`, drives visibility through all eight combinations, repoints the script
at another scene, exercises the scope settings, and asserts where the banners end
up, that a settled layout is not rewritten, and that a missing scene moves
nothing.

Needs `lupa` (Lua embedded in Python): `pip install lupa`. Skips without it -
OBS itself embeds Lua 5.3/5.4, and this runs on whatever lupa ships. It is the
only check that catches a Lua syntax error before OBS refuses to load the file.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

try:
    import lupa
except ImportError:  # pragma: no cover - the suite still runs without it
    lupa = None

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / 'obs' / 'chyrons-reflow.lua'

spec = importlib.util.spec_from_file_location('obs_reflow', ROOT / 'server/obs_reflow.py')
reflow = importlib.util.module_from_spec(spec)
sys.modules.setdefault('obs_reflow', reflow)
spec.loader.exec_module(reflow)


class FakeObs:
    """The obslua calls the script makes, backed by Lua tables.

    Writes and property registrations are counted, so a test can assert the
    script neither rewrites a settled layout nor disagrees with its own settings
    keys.
    """

    # The enums the script reads off `obs`.
    OBS_COMBO_TYPE_EDITABLE = 2
    OBS_COMBO_FORMAT_STRING = 0
    OBS_EDITABLE_LIST_TYPE_STRINGS = 0

    def __init__(self, lua, scenes, on_air=True, program_scene='Portrait'):
        self.lua = lua
        self.scenes = scenes                # {scene name: {item name: item table}}
        self.on_air = on_air
        self.program_scene = program_scene
        self.writes = 0
        self.properties = []                # [(name, [list entries])]
        self._current_property = None

    # -- sources and scenes ----------------------------------------------------
    def obs_get_source_by_name(self, name):
        name = str(name)
        return self.lua.table_from({'name': name}) if name in self.scenes else None

    def obs_scene_from_source(self, source):
        return source

    def obs_source_release(self, source):
        return None

    def obs_source_get_name(self, source):
        return source['name']

    def obs_scene_find_source(self, scene, name):
        # self.scenes is a plain dict: a Lua-table proxy would resolve .get to
        # Lua's own (absent) `get` and blow up with a confusing TypeError.
        return self.scenes[str(scene['name'])].get(str(name))

    def obs_enum_scenes(self):
        return self.lua.table_from([self.lua.table_from({'name': name}) for name in self.scenes])

    def obs_frontend_get_current_scene(self):
        if self.program_scene is None:
            return None
        return self.lua.table_from({'name': self.program_scene})

    def source_list_release(self, source_list):
        return None

    # -- scene items -----------------------------------------------------------
    def vec2(self):
        """OBS hands the script a mutable vector2; a Lua table behaves the same."""
        return self.lua.table_from({'x': 0.0, 'y': 0.0})

    def obs_sceneitem_visible(self, item):
        return bool(item['visible'])

    def obs_sceneitem_get_pos(self, item, vec):
        vec['x'] = item['x']
        vec['y'] = item['y']

    def obs_sceneitem_set_pos(self, item, vec):
        self.writes += 1
        item['x'] = vec['x']
        item['y'] = vec['y']

    def obs_source_active(self, source):
        """obs_source_active: shown in the final mix (the on-air gate)."""
        return self.on_air

    # -- properties and settings ----------------------------------------------
    def obs_properties_create(self):
        return self.lua.table_from({})

    def _record(self, props, name):
        self._current_property = (str(name), [])
        self.properties.append(self._current_property)
        return self.lua.table_from({'name': name})

    def obs_properties_add_list(self, props, name, description, kind, fmt):
        return self._record(props, name)

    def obs_properties_add_bool(self, props, name, description):
        return self._record(props, name)

    def obs_properties_add_editable_list(self, props, name, description, kind, filt, default):
        return self._record(props, name)

    def obs_property_list_add_string(self, prop, label, value):
        self._current_property[1].append(str(label))
        return True

    def obs_data_get_string(self, settings, key):
        return settings[key]

    def obs_data_get_bool(self, settings, key):
        return bool(settings[key])

    def obs_data_set_default_string(self, settings, key, value):
        return None

    def obs_data_set_default_bool(self, settings, key, value):
        return None

    def obs_data_get_array(self, settings, key):
        return settings[key]

    def obs_data_array_count(self, array):
        return len(array) if array is not None else 0

    def obs_data_array_item(self, array, index):
        return array[index + 1]             # Lua arrays are 1-based

    def obs_data_array_release(self, array):
        return None


@unittest.skipIf(lupa is None, 'lupa is not installed (pip install lupa)')
class LuaScriptBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.lua = lupa.LuaRuntime()
        g = self.lua.globals()
        self.scenes = {
            name: {item: self.lua.table_from({'visible': True, 'x': 0.0, 'y': 0.0})
                   for item in reflow.SOURCE_NAMES.values()}
            for name in ('Chryons', 'Backup Chryons')
        }
        self.log = []
        self.obs = FakeObs(self.lua, self.scenes)
        g.obslua = self.obs
        # Capture the script's own log lines instead of letting them hit stdout.
        g.print = lambda *parts: self.log.append(' '.join(str(p) for p in parts))
        self.lua.execute(SCRIPT_PATH.read_text())
        self.assertEqual(self.lua.globals().script_load(None), None)

    # -- helpers ---------------------------------------------------------------
    def update(self, **settings):
        """Drive script_update the way OBS does, with its settings table."""
        def value(v):
            if isinstance(v, list):
                return self.lua.table_from([self.lua.table_from({'value': entry}) for entry in v])
            return v

        self.lua.globals().script_update(self.lua.table_from(
            {key: value(v) for key, v in settings.items()}))

    def items(self, scene='Chryons'):
        return self.scenes[scene]

    def set_visible(self, *agents, scene='Chryons'):
        for agent, name in reflow.SOURCE_NAMES.items():
            self.items(scene)[name]['visible'] = agent in agents

    def tick(self, seconds=0.3):
        self.lua.globals().script_tick(seconds)

    def positions(self, scene='Chryons'):
        return {agent: self.items(scene)[name]['y'] for agent, name in reflow.SOURCE_NAMES.items()}

    # -- layout ----------------------------------------------------------------
    def test_every_visibility_combination_lands_on_the_row_layout_says(self):
        for mask in range(2 ** len(reflow.ORDER)):
            shown = [agent for position, agent in enumerate(reflow.ORDER) if mask & (1 << position)]
            self.set_visible(*shown)
            self.tick()
            expected_rows = reflow.row_layout(shown)
            expected = {agent: expected_rows[reflow.SOURCE_NAMES[agent]] for agent in reflow.ORDER}
            self.assertEqual(self.positions(), expected,
                             f'mask {mask} ({shown}): script placed {self.positions()}')

    def test_parking_a_hidden_banner_leaves_it_hidden(self):
        """OBS owns visibility: the script parks a hidden banner, never re-shows it."""
        self.set_visible('codex')             # the operator hid claude and hermes
        self.tick()
        self.assertEqual(self.positions()['claude'], reflow.PARK_Y)
        self.assertFalse(self.items()[reflow.SOURCE_NAMES['claude']]['visible'],
                         'the script must not re-enable what the operator hid')
        self.assertFalse(hasattr(self.obs, 'obs_sceneitem_set_visible'),
                         'the stub has no set_visible on purpose: the script must never toggle one')

    def test_two_up_flow_to_the_top_whichever_two_they_are(self):
        for pair in (('codex', 'claude'), ('codex', 'hermes'), ('claude', 'hermes')):
            self.set_visible(*pair)
            self.tick()
            positions = self.positions()
            self.assertEqual(positions[pair[0]], reflow.TOP, f'{pair} did not start at the top')
            self.assertEqual(positions[pair[1]], reflow.TOP + reflow.ROW + reflow.GAP,
                             f'{pair} left a gap: {positions}')

    def test_a_settled_layout_is_not_rewritten(self):
        self.set_visible('codex', 'claude')
        self.tick()
        writes = self.obs.writes
        self.assertGreater(writes, 0)
        for _ in range(5):
            self.tick()
        self.assertEqual(self.obs.writes, writes, 'a settled layout must not be rewritten every tick')

    def test_the_write_is_logged_once_per_move(self):
        self.set_visible('claude', 'hermes')
        self.tick()
        self.assertTrue(any('claude->40' in line and 'hermes->268' in line for line in self.log),
                        f'said {self.log}')
        self.log.clear()
        self.tick()
        self.assertEqual([line for line in self.log if '->' in line], [],
                         'a settled layout must not log moves it did not make')

    def test_a_missing_scene_moves_nothing_and_says_so_once(self):
        self.obs.scenes = {}                   # collection without the Chryons scene
        for _ in range(5):
            self.tick()
        self.assertEqual(self.obs.writes, 0, 'a missing scene must not move anything')
        waiting = [line for line in self.log if 'waiting for' in line]
        self.assertEqual(len(waiting), 1, f'should say it once, said {len(waiting)}: {self.log}')

    def test_it_settles_before_the_poll_interval_elapses(self):
        self.set_visible('hermes')
        self.tick(seconds=0.05)               # below POLL_SECONDS: nothing should have moved yet
        self.assertEqual(self.positions()['hermes'], 0.0)
        self.tick(seconds=0.3)
        self.assertEqual(self.positions()['hermes'], reflow.TOP)

    # -- scope: which scene, and when -----------------------------------------
    def test_the_banner_scene_can_be_repointed(self):
        self.update(scene='Backup Chryons', only_when_on_air=False)
        self.set_visible('hermes', scene='Backup Chryons')
        self.tick()
        self.assertEqual(self.positions('Backup Chryons')['hermes'], reflow.TOP,
                         'the script must act on the scene it was pointed at')
        self.assertEqual(self.positions('Chryons')['hermes'], 0.0,
                         'and leave the other scene alone')

    def test_an_empty_scene_list_means_anywhere(self):
        self.update(scene='Chryons', only_when_on_air=False, only_in_scenes=[])
        for program in ('Portrait', 'Token Bowl Live Coding', 'Anything'):
            self.obs.program_scene = program
            self.set_visible('codex')
            self.tick()
            self.assertEqual(self.positions()['codex'], reflow.TOP, f'blocked while in {program}')

    def test_a_scene_list_limits_the_reflow_to_those_scenes(self):
        self.update(scene='Chryons', only_when_on_air=False,
                    only_in_scenes=['Livestream - Demo', 'Token Bowl Live'])
        self.obs.program_scene = 'Portrait'
        self.set_visible('claude', 'hermes')
        for _ in range(3):
            self.tick()
        self.assertEqual(self.obs.writes, 0, 'a scene outside the list must not be reflowed')
        self.assertTrue(any('paused' in line for line in self.log), f'said {self.log}')

        self.obs.program_scene = 'Livestream - Demo'
        self.tick()                            # one poll after switching in
        self.assertEqual(self.positions()['claude'], reflow.TOP)
        self.assertEqual(self.positions()['hermes'], reflow.TOP + reflow.ROW + reflow.GAP)

    def test_the_on_air_gate_still_holds_on_its_own(self):
        self.update(scene='Chryons', only_when_on_air=True, only_in_scenes=[])
        self.obs.on_air = False
        self.set_visible('claude', 'hermes')
        for _ in range(3):
            self.tick()
        self.assertEqual(self.obs.writes, 0, "off air is not this script's business")
        self.obs.on_air = True
        self.tick()
        self.assertEqual(self.positions()['claude'], reflow.TOP)
        self.assertTrue(any('active in' in line for line in self.log), f'said {self.log}')

    def test_defaults_scope_the_reflow_to_the_named_scene(self):
        """Untouched settings must not start moving things in other scenes."""
        self.update(scene='Chryons', only_when_on_air=True)
        self.obs.on_air = False
        self.set_visible('codex')
        self.tick()
        self.assertEqual(self.obs.writes, 0)

    # -- the properties OBS renders -------------------------------------------
    def test_the_panel_offers_a_scene_picker_and_the_scope_settings(self):
        self.lua.globals().script_properties()
        names = [name for name, _ in self.obs.properties]
        self.assertEqual(names, ['scene', 'only_when_on_air', 'only_in_scenes'],
                         'every name here must match what script_update reads')
        picker = dict(self.obs.properties)['scene']
        self.assertEqual(sorted(picker), sorted(self.scenes),
                         'the scene picker must list the scenes in the collection')

    def test_script_defaults_names_the_generated_scene(self):
        defaults = []
        self.obs.obs_data_set_default_string = lambda settings, key, value: defaults.append((key, value))
        self.obs.obs_data_set_default_bool = lambda settings, key, value: defaults.append((key, value))
        self.lua.globals().script_defaults(self.lua.table_from({}))
        self.assertIn(('scene', reflow.SCENE), defaults)
        self.assertIn(('only_when_on_air', True), defaults)

    def test_the_description_renders(self):
        """OBS renders this in the Scripts dialog: a Lua error here is a blank entry."""
        described = self.lua.globals().script_description()
        self.assertIn(reflow.SCENE, described)
        self.assertIn(str(reflow.TOP), described)
        self.assertIn('GENERATED', described)


if __name__ == '__main__':
    unittest.main()

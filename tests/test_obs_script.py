"""The generated OBS Lua script must agree with the Python reflow.

The Lua cannot run here (OBS embeds its own interpreter), so the part that can
be wrong silently - the layout rule - is not written in Lua at all: the
generator emits it as a lookup table built from `row_layout()`. These tests
regenerate that table and compare it, combination by combination, against the
function the Python reflow and its unit tests use, and fail if the file on disk
is stale.
"""
import importlib.util
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('obs_reflow', ROOT / 'server/obs_reflow.py')
reflow = importlib.util.module_from_spec(spec)
sys.modules.setdefault('obs_reflow', reflow)
spec.loader.exec_module(reflow)

spec = importlib.util.spec_from_file_location('build_obs_script', ROOT / 'scripts/build_obs_script.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

SCRIPT_PATH = ROOT / 'obs' / 'chyrons-reflow.lua'


def lua_table(text, name):
    """Parse a flat `local NAME = { ... }` block from the generated Lua."""
    match = re.search(rf'^local {name} = \{{(.*?)^\}}', text, re.S | re.M)
    if not match:
        raise AssertionError(f'no `local {name}` table in the generated script')
    return match.group(1)


def layout_from_script(text):
    """{mask: [y per agent in ORDER]} as emitted."""
    body = lua_table(text, 'LAYOUT')
    table = {}
    for mask, values in re.findall(r'\[(\d+)\] = \{([^}]*)\}', body):
        table[int(mask)] = [int(v.strip()) for v in values.split(',')]
    return table


class GeneratedScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = builder.build()

    def test_the_emitted_table_matches_row_layout_for_every_combination(self):
        table = layout_from_script(self.script)
        masks = list(range(2 ** len(reflow.ORDER)))
        self.assertEqual(sorted(table), masks, 'every visibility combination must be covered')
        for mask in masks:
            visible = [agent for position, agent in enumerate(reflow.ORDER) if mask & (1 << position)]
            rows = reflow.row_layout(visible)
            expected = [rows[reflow.SOURCE_NAMES[agent]] for agent in reflow.ORDER]
            self.assertEqual(table[mask], expected,
                             f'mask {mask} ({visible or "none"}) disagrees with row_layout()')

    def test_hiding_the_top_banner_lifts_the_rest(self):
        """The behaviour the whole change is about: two up means rows 1 and 2."""
        table = layout_from_script(self.script)
        codex, claude, hermes = (reflow.ORDER.index(a) for a in ('codex', 'claude', 'hermes'))
        # codex hidden, claude + hermes showing -> claude takes the top row
        self.assertEqual(table[2 | 4][claude], reflow.TOP)
        self.assertEqual(table[2 | 4][hermes], reflow.TOP + reflow.ROW + reflow.GAP)
        self.assertEqual(table[2 | 4][codex], reflow.PARK_Y)
        # the top one hidden, the bottom two showing: no gap is left behind
        self.assertEqual(table[1 | 4][codex], reflow.TOP)
        self.assertEqual(table[1 | 4][hermes], reflow.TOP + reflow.ROW + reflow.GAP)

    def test_the_script_names_the_scene_and_sources_the_module_uses(self):
        self.assertIn(f'local DEFAULT_SCENE = "{reflow.SCENE}"', self.script)
        for agent, name in reflow.SOURCE_NAMES.items():
            self.assertIn(f'{agent} = "{name}"', self.script)
        self.assertIn('local ORDER = {' + ', '.join(f'"{a}"' for a in reflow.ORDER) + '}', self.script)

    def test_bit_values_follow_the_order_the_banners_stack_in(self):
        """LAYOUT's index is the set of visible banners, so bit i must be ORDER[i]."""
        body = lua_table(self.script, 'BITS')
        bits = {agent: int(value) for agent, value in re.findall(r'(\w+) = (\d+)', body)}
        for position, agent in enumerate(reflow.ORDER):
            self.assertEqual(bits[agent], 1 << position, f'{agent} is not bit {position}')
        self.assertNotIn('ROW + GAP', self.script, 'the row arithmetic belongs in the table, not the Lua')

    def test_it_reacts_faster_than_a_second(self):
        poll = re.search(r'^local POLL_SECONDS = ([\d.]+)', self.script, re.M)
        self.assertIsNotNone(poll, 'the poll interval must be generated')
        self.assertLessEqual(float(poll.group(1)), 0.5,
                             'a slower poll is visible as lag between a key press and the rows moving')

    def test_the_lua_only_writes_position_and_only_when_it_differs(self):
        self.assertIn('obs_sceneitem_set_pos', self.script)
        self.assertIn('obs_sceneitem_visible', self.script)
        self.assertNotIn('obs_sceneitem_set_visible', self.script,
                         'OBS owns visibility; the script must never toggle it')
        self.assertIn('math.abs(banner_y(items[agent]) - y) > 0.5', self.script)

    def test_the_file_on_disk_is_current(self):
        self.assertTrue(SCRIPT_PATH.is_file(),
                        f'{SCRIPT_PATH} is missing: run scripts/build_obs_script.py')
        on_disk = SCRIPT_PATH.read_text()
        self.assertEqual(on_disk, self.script,
                         'obs/chyrons-reflow.lua is stale: re-run scripts/build_obs_script.py')

    def test_the_scope_settings_are_generated(self):
        """One predicate decides whether a tick may move anything, and it is both
        gates: the banner scene being on air, and the program scene being allowed."""
        self.assertIn('local function paused_because()', self.script)
        self.assertIn('if only_when_on_air and air == false then', self.script)
        self.assertIn('if not in_scene_list(program) then', self.script)
        self.assertIn('obs_source_active', self.script)
        # A missing scene is "unknown", not "off air": it has to fall through to
        # the report rather than being silently skipped like a scope pause.
        self.assertIn('if source == nil then return nil end', self.script)

    def test_the_pickers_and_their_defaults_are_generated(self):
        self.assertIn('obs_properties_add_list(props, "scene"', self.script)
        self.assertIn('obs.OBS_COMBO_TYPE_EDITABLE', self.script)
        self.assertIn('obs_enum_scenes()', self.script)
        self.assertIn('obs_properties_add_bool(props, "only_when_on_air"', self.script)
        self.assertIn('obs_properties_add_editable_list(props, "only_in_scenes"', self.script)
        self.assertIn('obs_data_set_default_string(settings, "scene", DEFAULT_SCENE)', self.script)
        self.assertIn('obs_data_set_default_bool(settings, "only_when_on_air", true)', self.script)
        # The names script_properties registers must be the names script_update reads.
        for key in ('scene', 'only_when_on_air', 'only_in_scenes'):
            self.assertIn(f'"{key}"', self.script,
                          'script_properties and script_update must agree on the key names')

    def test_the_generator_is_idempotent(self):
        self.assertEqual(builder.build(), builder.build())


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Generate the OBS Lua reflow script from server/obs_reflow.py.

OBS runs Lua scripts in-process, so the layout can live inside OBS with no
daemon, no thread of ours and no websocket subscription: the script reacts on
the next frame and it lives and dies with OBS.

The rule ("which row does this banner get, given what else is visible") is NOT
re-implemented in Lua. This generator writes it out as a lookup table, built
from the same `row_layout()` the Python reflow and its unit tests use, so the
numbers in the script cannot drift from the tested ones - `tests/test_obs_script.py`
regenerates the table and compares it against `row_layout()` for all eight
visibility combinations.

    python3 scripts/build_obs_script.py                 # write obs/chyrons-reflow.lua
    python3 scripts/build_obs_script.py --out /path.lua  # somewhere else
    python3 scripts/build_obs_script.py --check          # non-zero if the file is stale
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'obs' / 'chyrons-reflow.lua'
# How often the script re-reads visibility. OBS calls it every frame; this is the
# delay the user actually sees between pressing a deck key and the rows moving.
POLL_SECONDS = 0.2


def reflow_module():
    """server/obs_reflow.py, loaded by path (same way the tests load it)."""
    spec = importlib.util.spec_from_file_location('obs_reflow', ROOT / 'server/obs_reflow.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('obs_reflow', module)
    spec.loader.exec_module(module)
    return module


def layout_table(reflow):
    """{bitmask: [y for each agent in ORDER]}.

    codex is bit 1, claude bit 2, hermes bit 4 - i.e. the position in ORDER, so
    the table reads left to right the same way the banners stack top to bottom.
    """
    table = {}
    for index, combination in enumerate(itertools.product((0, 1), repeat=len(reflow.ORDER))):
        mask = sum(bit << position for position, bit in enumerate(combination))
        visible = [agent for agent, bit in zip(reflow.ORDER, combination) if bit]
        rows = reflow.row_layout(visible)
        table[mask] = [rows[reflow.SOURCE_NAMES[agent]] for agent in reflow.ORDER]
    return table


def render(reflow) -> str:
    table = layout_table(reflow)
    order = ', '.join(f'"{agent}"' for agent in reflow.ORDER)
    sources = ',\n  '.join(f'{agent} = "{reflow.SOURCE_NAMES[agent]}"' for agent in reflow.ORDER)
    bits = ', '.join(f'{agent} = {bit}' for agent, bit in
                     ((agent, 1 << position) for position, agent in enumerate(reflow.ORDER)))
    rows = []
    for mask in sorted(table):
        values = ', '.join(str(int(y)) for y in table[mask])
        naming = '+'.join(agent for position, agent in enumerate(reflow.ORDER) if mask & (1 << position))
        rows.append(f'  [{mask}] = {{{values}}},   -- {naming or "none visible"}')
    return TEMPLATE.format(
        scene=reflow.SCENE,
        order=order,
        sources=sources,
        bits=bits,
        layout='\n'.join(rows),
        top=int(reflow.TOP),
        row=int(reflow.ROW),
        gap=int(reflow.GAP),
        park=int(reflow.PARK_Y),
        poll=POLL_SECONDS,
    )


TEMPLATE = '''-- Chyron reflow for OBS Studio.
--
-- GENERATED FILE - edit server/obs_reflow.py and re-run
-- `python3 scripts/build_obs_script.py`, not this file. The layout table below
-- is emitted from that module's row_layout(), which is the same function the
-- Python reflow and its unit tests use, so the numbers cannot drift.
--
-- Why this exists: OBS runs Lua scripts in-process, so keeping the banners
-- stacked needs no daemon, no background thread of ours and no websocket
-- subscription - and nothing to restart when it changes. Add it once under
-- Tools -> Scripts (it is a per-scene-collection setting, so check that on each
-- collection you stream).
--
-- OBS owns visibility: the eye icon, a deck key or the websocket API toggles a
-- banner's scene item. This script only ever writes position, only for the
-- banners it can resolve, and only when the value actually differs.

local obs = obslua

local DEFAULT_SCENE = "{scene}"   -- the generated default; change it in the script's properties
local banner_scene = DEFAULT_SCENE
local ORDER = {{{order}}}
local SOURCES = {{
  {sources},
}}
-- Bit per agent, in ORDER: the index into LAYOUT is the set of visible banners.
local BITS = {{{bits}}}

-- y per banner for every possible set of visible banners. Hidden ones park
-- off-canvas instead of being resized, so their browser sources keep running and
-- a reveal is instant.
local LAYOUT = {{
{layout}
}}

-- script_tick() runs every frame and hands us the delta, so poll on elapsed
-- time rather than frames: {poll} seconds notices a keypress immediately to a
-- human eye while keeping the scene lookups off the per-frame path.
local POLL_SECONDS = {poll}

local elapsed = 0
local placed = {{}}          -- agent -> the y we last asked for
local missing = false       -- scene or banner gone: said so once, then quiet
-- Scope, all of it live-editable in Tools -> Scripts:
--   banner_scene      the scene holding the banners
--   only_when_on_air  pause unless that scene is in the final mix
--   only_in_scenes    pause unless the program scene is one of these (empty = anywhere)
local only_when_on_air = true
local only_in_scenes = {{}}
local watching = nil        -- nil until the first tick tells us which side of the gate we are on

local function on_air()
  local source = obs.obs_get_source_by_name(banner_scene)
  if source == nil then return nil end   -- no such scene: reported below, not skipped
  -- obs_source_active: shown in the final mix. (obs_source_showing, by
  -- contrast, is true for a preview or multiview too.)
  local active = obs.obs_source_active(source)
  obs.obs_source_release(source)
  return active
end

local function current_program_scene()
  local source = obs.obs_frontend_get_current_scene()
  if source == nil then return nil end
  local name = obs.obs_source_get_name(source)
  obs.obs_source_release(source)
  return name
end

local function in_scene_list(name)
  if #only_in_scenes == 0 then return true end
  for _, wanted in ipairs(only_in_scenes) do
    if wanted == name then return true end
  end
  return false
end

-- Returns nil to reflow, or a short reason to stay put.
local function paused_because()
  local air = on_air()
  if only_when_on_air and air == false then
    return "off air"
  end
  local program = current_program_scene()
  if not in_scene_list(program) then
    return "program scene is " .. tostring(program)
  end
  return nil
end

local function scene_item(agent)
  local source = obs.obs_get_source_by_name(banner_scene)
  if source == nil then return nil end
  local scene = obs.obs_scene_from_source(source)   -- borrowed: no release
  obs.obs_source_release(source)
  if scene == nil then return nil end
  -- Also borrowed: obs_scene_find_source does not increment the reference.
  return obs.obs_scene_find_source(scene, SOURCES[agent])
end

local function banner_y(item)
  local position = obs.vec2()
  obs.obs_sceneitem_get_pos(item, position)
  return position.y
end

local function place(item, y)
  local position = obs.vec2()
  position.x = 0        -- the Python reflow pins x too, so both writers agree
  position.y = y
  obs.obs_sceneitem_set_pos(item, position)
end

function script_description()
  return table.concat({{
    "Keeps the Token Chyron banners stacked from the top of the '" .. banner_scene .. "' scene.",
    "",
    "OBS owns visibility - the eye icon, a deck key or the websocket API toggles a banner - and " ..
      "this re-stacks the visible ones with no gaps, parking the hidden ones off-canvas so their " ..
      "browsers stay warm and current.",
    "",
    "Rows: top " .. {top} .. ", row height " .. {row} .. ", gap " .. {gap} ..
      ", parked at " .. {park} .. " (canvas pixels).",
    "",
    "Only the " .. banner_scene .. " scene's banner items are touched, and only their " ..
      "position. Untick the on-air option to keep the stack correct from scenes the " ..
      "chyrons are not shown in.",
    "GENERATED from server/obs_reflow.py by scripts/build_obs_script.py.",
  }}, "\\n")
end

local function scene_list(settings)
-- The editable list arrives as an array of {{value = "Scene name"}} rows.
  local names = {{}}
  local rows = obs.obs_data_get_array(settings, "only_in_scenes")
  if rows == nil then return names end
  for index = 1, obs.obs_data_array_count(rows) do
    local row = obs.obs_data_array_item(rows, index - 1)
    local name = obs.obs_data_get_string(row, "value")
    if name ~= nil and name ~= "" then table.insert(names, name) end
  end
  obs.obs_data_array_release(rows)
  return names
end

function script_defaults(settings)
  obs.obs_data_set_default_string(settings, "scene", DEFAULT_SCENE)
  obs.obs_data_set_default_bool(settings, "only_when_on_air", true)
end

function script_properties()
  local props = obs.obs_properties_create()

  -- Same picker shape as the other OBS scripts on this machine: an editable list
  -- of scenes, so a renamed or not-yet-created scene can still be typed in.
  local picker = obs.obs_properties_add_list(props, "scene", "Scene with the banner items",
    obs.OBS_COMBO_TYPE_EDITABLE, obs.OBS_COMBO_FORMAT_STRING)
  local scenes = obs.obs_enum_scenes()
  if scenes ~= nil then
    for _, scene in ipairs(scenes) do
      local name = obs.obs_source_get_name(scene)
      obs.obs_property_list_add_string(picker, name, name)
    end
    obs.source_list_release(scenes)
  end

  obs.obs_properties_add_bool(props, "only_when_on_air",
    "Only reflow while that scene is on air")
  obs.obs_properties_add_editable_list(props, "only_in_scenes",
    "Only reflow in these scenes (empty = anywhere)",
    obs.OBS_EDITABLE_LIST_TYPE_STRINGS, "", "")

  return props
end

function script_update(settings)
  local chosen = obs.obs_data_get_string(settings, "scene")
  banner_scene = (chosen ~= nil and chosen ~= "") and chosen or DEFAULT_SCENE
  only_when_on_air = obs.obs_data_get_bool(settings, "only_when_on_air")
  only_in_scenes = scene_list(settings)
  -- A changed scope means the layout may need settling again: drop the cache so
  -- the next tick re-reads every banner instead of trusting what we last wrote.
  placed = {{}}
  watching = nil
end

function script_load(settings)
  elapsed = 0
  placed = {{}}
  missing = false
  print("chyron reflow: loaded (top " .. {top} .. ", row " .. {row} .. ", gap " .. {gap} ..
        ", park " .. {park} .. ")")
end

function script_unload()
  print("chyron reflow: unloaded")
end

function script_tick(seconds)
  elapsed = elapsed + (seconds or 0)
  if elapsed < POLL_SECONDS then return end
  elapsed = 0

  local pause = paused_because()
  if pause ~= nil then
    -- Out of scope: leave the rows exactly as they are. Re-checked every tick,
    -- so the layout settles within one poll of coming back.
    if watching ~= false then
      print("chyron reflow: paused (" .. pause .. ")")
    end
    watching = false
    return
  end
  if watching ~= true then
    print("chyron reflow: active in " .. banner_scene)
    placed = {{}}          -- re-read every banner: we have not been watching
  end
  watching = true

  -- Resolve the whole set before moving anything: one banner we cannot find
  -- means "leave the layout alone" rather than parking the rest.
  local items, mask = {{}}, 0
  for index, agent in ipairs(ORDER) do
    items[agent] = scene_item(agent)
    if items[agent] == nil then
      if not missing then
        print("chyron reflow: waiting for '" .. banner_scene .. "' / '" .. SOURCES[agent] .. "'")
        missing = true
      end
      return
    end
    if obs.obs_sceneitem_visible(items[agent]) then
      mask = mask + BITS[agent]
    end
  end
  if missing then
    print("chyron reflow: " .. banner_scene .. " is back")
    missing = false
  end

  local wanted = LAYOUT[mask]
  local moved = {{}}
  for index, agent in ipairs(ORDER) do
    local y = wanted[index]
    if placed[agent] ~= y then
      if math.abs(banner_y(items[agent]) - y) > 0.5 then
        place(items[agent], y)
        table.insert(moved, agent .. "->" .. y)
      end
      placed[agent] = y
    end
  end
  if #moved > 0 then
    print("chyron reflow: " .. table.concat(moved, " "))
  end
end
'''


def build() -> str:
    return render(reflow_module())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default=str(DEFAULT_OUT), help='where to write the script')
    parser.add_argument('--check', action='store_true',
                        help='do not write; exit non-zero if the file is stale')
    args = parser.parse_args()

    out = Path(args.out)
    script = build()
    if args.check:
        current = out.read_text() if out.is_file() else None
        if current != script:
            print(f'STALE: {out} does not match server/obs_reflow.py')
            return 1
        print(f'up to date: {out}')
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(script)
    reflow = reflow_module()
    print(f'wrote {out} ({len(script)} bytes, {len(reflow.ORDER)} banners, '
          f'{len(layout_table(reflow))} visibility combinations)')
    print(f'add it in OBS: Tools -> Scripts -> + -> {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

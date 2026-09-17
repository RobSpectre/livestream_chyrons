-- Chyron reflow for OBS Studio.
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

local DEFAULT_SCENE = "Chryons"   -- the generated default; change it in the script's properties
local banner_scene = DEFAULT_SCENE
local ORDER = {"codex", "claude", "hermes"}
local SOURCES = {
  codex = "Token Chyron - Codex",
  claude = "Token Chyron - Claude",
  hermes = "Token Chyron - Hermes",
}
-- Bit per agent, in ORDER: the index into LAYOUT is the set of visible banners.
local BITS = {codex = 1, claude = 2, hermes = 4}

-- y per banner for every possible set of visible banners. Hidden ones park
-- off-canvas instead of being resized, so their browser sources keep running and
-- a reveal is instant.
local LAYOUT = {
  [0] = {-400, -400, -400},   -- none visible
  [1] = {40, -400, -400},   -- codex
  [2] = {-400, 40, -400},   -- claude
  [3] = {40, 268, -400},   -- codex+claude
  [4] = {-400, -400, 40},   -- hermes
  [5] = {40, -400, 268},   -- codex+hermes
  [6] = {-400, 40, 268},   -- claude+hermes
  [7] = {40, 268, 496},   -- codex+claude+hermes
}

-- script_tick() runs every frame and hands us the delta, so poll on elapsed
-- time rather than frames: 0.2 seconds notices a keypress immediately to a
-- human eye while keeping the scene lookups off the per-frame path.
local POLL_SECONDS = 0.2

local elapsed = 0
local placed = {}          -- agent -> the y we last asked for
local missing = false       -- scene or banner gone: said so once, then quiet
-- Scope, all of it live-editable in Tools -> Scripts:
--   banner_scene      the scene holding the banners
--   only_when_on_air  pause unless that scene is in the final mix
--   only_in_scenes    pause unless the program scene is one of these (empty = anywhere)
local only_when_on_air = true
local only_in_scenes = {}
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
  return table.concat({
    "Keeps the Token Chyron banners stacked from the top of the '" .. banner_scene .. "' scene.",
    "",
    "OBS owns visibility - the eye icon, a deck key or the websocket API toggles a banner - and " ..
      "this re-stacks the visible ones with no gaps, parking the hidden ones off-canvas so their " ..
      "browsers stay warm and current.",
    "",
    "Rows: top " .. 40 .. ", row height " .. 220 .. ", gap " .. 8 ..
      ", parked at " .. -400 .. " (canvas pixels).",
    "",
    "Only the " .. banner_scene .. " scene's banner items are touched, and only their " ..
      "position. Untick the on-air option to keep the stack correct from scenes the " ..
      "chyrons are not shown in.",
    "GENERATED from server/obs_reflow.py by scripts/build_obs_script.py.",
  }, "\n")
end

local function scene_list(settings)
-- The editable list arrives as an array of {value = "Scene name"} rows.
  local names = {}
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
  placed = {}
  watching = nil
end

function script_load(settings)
  elapsed = 0
  placed = {}
  missing = false
  print("chyron reflow: loaded (top " .. 40 .. ", row " .. 220 .. ", gap " .. 8 ..
        ", park " .. -400 .. ")")
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
    placed = {}          -- re-read every banner: we have not been watching
  end
  watching = true

  -- Resolve the whole set before moving anything: one banner we cannot find
  -- means "leave the layout alone" rather than parking the rest.
  local items, mask = {}, 0
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
  local moved = {}
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

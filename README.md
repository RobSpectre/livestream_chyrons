# Token Chyrons

Three wide, 4K-sized banners showing live coding-agent usage on stream: turn
state, tokens/sec, context window left, session tokens, 30-day tokens, the model
in use and a sparkline of the last hour's token consumption for **Codex**,
**Claude** and **Hermes**. Vue 3 + Vite + Tailwind v4 with shadcn-vue components
on a solid chroma-key background.

```
┌ CODEX ───────────────────────────────────────────────────────────────┐
│ MODEL │ 1H TOK ▁▃▅▂▇▁▃▅▂▇▁▃ │ TOK/S │ CTX │ SESS │ 30D                ● ACTIVE │  white
├ CLAUDE ──────────────────────────────────────────────────────────────┤
│ MODEL │ 1H TOK ▁▃▅▂▇▁▃▅▂▇▁▃ │ TOK/S │ CTX │ SESS │ 30D                ● IDLE   │  #d97757
├ HERMES ──────────────────────────────────────────────────────────────┤
│ MODEL │ 1H TOK ▁▃▅▂▇▁▃▅▂▇▁▃ │ TOK/S │ CTX │ SESS │ 30D                ● ACTIVE │  rgb(0,0,242)
└──────────────────────────────────────────────────────────────────────┘
```

## Data sources: the same methods as the OpenDeck Page 6 buttons

The counters come from exactly the modules that drive the Page 6 telemetry keys
(`party.hack.status.control` keys with `kind: codex_telemetry` /
`agent_telemetry` in
`~/.config/opendeck/profiles/sd-CL37L2A01125/06 - Page 6.json`), imported from
`/media/rspectre/Storage/workspace/streamdeck/tools` and never reimplemented:

| capsule | reader | source of truth |
| --- | --- | --- |
| Codex (all) | `tools.codex_telemetry.Telemetry` (`CodexAttention` + `RolloutUsage`) | `~/.codex` rollout JSONL, `state_5.sqlite`, desktop IPC socket, `codex app-server` rate limits |
| Claude (all) | `tools.agent_telemetry.AgentTelemetry` | `~/.claude/projects/**/*.jsonl` + the status-line hook cache in `~/.cache/opendeck-agents/claude` |
| Hermes (all) | `tools.agent_telemetry.AgentTelemetry` | `~/.hermes/state.db` + `~/.cache/opendeck-agents/hermes` |
| `MODEL` | `server/agent_extras.py` | Codex `turn_context.model` in the newest rollout, Claude `message.model` in the newest transcript, Hermes `sessions.model` |
| `1H TOK` | `server/agent_extras.py` | the same parsed events the telemetry objects already hold, binned into twenty-four 2.5-minute buckets |

Everything is read-only; the overlay never writes to an agent or to the cache
the plugin owns. Metric titles/details are passed through unchanged, so the
banners read identically to the deck keys. Only `context` is added (the deck's
sixth column is `reset` there): it is `contextWindow - last request` as a
percentage.

The hourly series is exact for Codex and Claude, which keep per-event token
records. `~/.hermes/state.db` keeps no per-message history - only one row per
(session, model, task) in `session_model_usage` with a first_seen/last_seen
interval - so the Hermes graph is those totals spread proportionally across the
buckets they cover. It is an estimate; the other two are not. Hermes totals use
the same canonical definition `tools.agent_telemetry.hermes_usage` applies to the
session and 30-day capsules (uncached input + cache read/write + output), so the
graph is on the same scale as the numbers beside it.

Quota is still in the API payload (`metrics.quota`) because the deck keys read
it, but a chyron no longer shows a quota capsule.

## Turn state: green, grey, red

The `TURN` chip in each banner header is colour-coded, with black ink on the flat
block colour (`src/lib/turnState.ts` decides, `AgentBanner.vue` renders):

| state | tone | fill | motion |
| --- | --- | --- | --- |
| `ACTIVE` | working | green `#4ade80` | grows and shrinks, 1.7s, to 1.055 |
| `WAITING` | wants a human | red `#ef4444` | grows and shrinks too |
| `IDLE`, `OFFLINE`, `OPEN`, `—` | nothing happening | grey `#d4d4d4` | still |

The classification is by intent, not an exact match, because the telemetry
vocabulary (`ACTIVE`/`WAITING`/`IDLE`/`OFFLINE`/`OPEN`) has no failure state yet:
anything reading as working is green, anything reading as waiting on input or
permission - or broken - is red, and everything else falls back to grey. Unknown
states go grey on purpose: a wrong green would claim work that is not happening.
The pulse is a transform, so it never reflows the header, `prefers-reduced-motion`
switches it off, and the chip carries `data-tone` for the tests. Contrast of black
on each fill is 12.1:1 green, 5.6:1 red, 14.2:1 grey.

## Style: neobrutalism

The look follows [neobrutalism.dev](https://www.neobrutalism.dev/docs) — the
same vocabulary it publishes as shadcn registry tokens (`border-border`,
`shadow-shadow: 4px 4px 0 0 black`, `rounded-base: 5px`), scaled for a 4K source
and re-expressed as CSS variables in `src/style.css`:

| library token | here (4K source) | on a 1080p canvas |
| --- | --- | --- |
| `border-2 border-border` | 0.06rem black border | 3px |
| `shadow-shadow` | 0.16rem 0.16rem 0 #000 | 8px 8px 0 |
| `rounded-base` (5px) | 0.12rem | 6px |
| chip frame | 0.045rem border + 0.07rem shadow | 2px + 3.5px |

Per banner the palette is three values, sent by the server so the chips, the
capsules and the text can never disagree:

- **accent** — the block colour of the header chips and all six capsules.
- **tint** — the light card those blocks sit on. A black frame and a black
  offset shadow only read against something lighter, so the card is light rather
  than the near-black it started as. Codex `#dcdcdc`, Claude `#f7e4dd`,
  Hermes `#ccccfc`.
- **ink** — the text colour on the accent block: black on Codex white (21:1) and
  Claude orange (6.7:1), white on Hermes blue (9.2:1). `tests/overlay-harness.mjs`
  fails the build below 4.5:1.

Banner colours stay as asked: Codex white, Claude `#d97757`, Hermes `#0000f2`.
Nothing in the palette is within 60 levels of the key green, which the harness
also asserts.

## Run it

```bash
npm install                                     # once
uv venv .venv && uv pip install pillow          # once, for the telemetry server
npm run build                                   # build the overlay into dist/
.venv/bin/python server/chyrons_server.py --port 8790 --dist dist
```

Then open <http://127.0.0.1:8790/>. During development `npm run dev` proxies
`/api` to the same server on 8790, so run both.

Flags: `--host`, `--port`, `--agents codex,claude` (skip readers for agents you
do not run; their banner then shows dashes), `--dist PATH`. `STREAMDECK_ROOT`
overrides the Stream Deck checkout the tools are imported from.

## OBS

Three browser sources are already in the hack.party collection – see
[OBS wiring](#obs-wiring-already-applied). To add another one by hand: Sources →
**+ Browser**, paste the URL (use `?agent=<name>` for a single banner), set the
size to 3840x240, leave "Shutdown source when not visible" off so the numbers
keep updating off-screen, then add a **Chroma Key** filter (green, similarity
~400, smoothness ~80, spill ~100). Put the source above your camera/scene
captures; the banner panels are fully opaque, so nothing of the key colour can
bleed through them.

Query parameters:

| url | result |
| --- | --- |
| `?agent=codex` | one banner only, fills its own browser source (`codex`, `claude`, `hermes`) |
| `?bg=green` | default key colour, `#00ff00` |
| `?bg=magenta` | magenta key – use if you have green in the scene |
| `?bg=blue`, `?bg=cyan` | other key presets |
| `?bg=transparent` | no backing colour, for compositing overlays elsewhere |
| `?bg=%2300b140` | any hex colour |
| `?demo=1` | offline sample data, labelled `DEMO`, for framing before you start |
| `?sound=1` | play the tally on increments (off by default) |
| `?tally=1` | audio-only page: draws nothing, just the tally (own OBS source) |
| `?volume=0.2` | tally level, 0-1 (default 0.14) |
| `?tallygap=60` | floor between clicks in ms, under the half-flap phase (default 20) |
| `?feed=manual` | stop the stream/poller; frames only arrive via the test hook |

While the page has focus you can also press `g`, `m`, `b` or `t` to switch the
key colour.

## Counting animation and tally

`SESS` and `30D` update like a rail-station split-flap board rather than easing
smoothly: every digit that changes rolls upwards, the old digit leaving through
the top of its cell while the new one rises from below. `CountUp.vue` advances
the value one *display step* per flap - the smallest change that alters the
string, so `209.1M` clatters through 0.1M at a time - on a ~48ms mechanical
cadence with a little jitter, capped at 16 flaps, then a longer settling flap.
A one- or two-flap change gets two extra decorative flips so a small update
still rattles rather than blinking.

Three rules it has to keep, and the harness covers all three:

- the settled text is character-for-character what the API sent (`src/lib/format.ts`
  parses and re-formats the compact string, `≥` markers included);
- the value only ever moves forward while rolling, so the displayed number never
  dips mid-animation - a fall (a new session) snaps silently instead;
- the digits stay real text: the outgoing digit is drawn as
  `::after { content: attr(data-prev) }`, which keeps it out of `textContent`.
  Each flap is timed to land inside its tick (42ms against a ~48ms cadence);
  the last one takes 110ms, which is the drum settling. `prefers-reduced-motion`
  turns the whole thing off, and `data-flaps` on the counter reports the roll for
  the tests.

The tally is synthesised with WebAudio - a short band-passed noise burst plus a
high partial, no audio asset. It clicks **once per flap of the counter drum**, so
a roll-up rattles like a tally clicker rather than firing a single ka-ching per
update: mid-roll clicks are lighter, the flap that lands is the full tally, and a
decorative settle flip sits between the two. Each click is detuned a little at
random, because a rack of identical clicks sounds synthetic.

- it is **opt-in** (`?sound=1`), so the visual sources stay silent by default;
- the schedule is shared: `@/lib/flaps` produces one plan that both the drum
  (`CountUp.vue`) and the sound (`useTallyFeed`) follow, so the clicks land on
  the flaps. It has to be shared - the audio-only source renders no capsules, so
  there is no drum there to listen to;
- `?tallygap` is the **floor between clicks** (default 20ms). It only catches
  rolls landing on the same millisecond, so it has to stay below the phase below
  or it would swallow real clicks;
- an agent's two counters are **de-phased by half a flap** (24ms): session and
  30-day count the same tokens and so move together, and without the offset every
  one of their clicks would land on the same millisecond and the floor would
  throw half of them away;
- visibility is re-checked as each click fires, so hiding the chyrons mid-roll
  silences the rest of that roll instead of letting it click on;
- it is **silent whenever the chyron is not on screen** (see below);
- the page posts its AudioContext state to `/api/tally-state`, surfaced in
  `/api/health` as `tally` - along with `plays`, `rolls` and the last click
  `gap`, which is how the rattle gets measured rather than assumed. The tally
  source in this scene reports `{"state": "running", "plays": N,
  "page": "audio-only"}`. Reports are kept per page identity (`tally_reports`)
  and `tally` prefers the audio-only source, so a test browser playing tallies
  can never be mistaken for the OBS audio source.

Mute it in OBS with the **Token Chyron - Tally** source's audio (Audio Mixer →
mute), or drop `?sound=1` from its URL.

### Nothing plays when the chyron is not on screen

A hidden chyron makes no sound, and a scene that is not on air makes no sound at
all. The reflow service already knows both facts, so it publishes them and the
page obeys them:

- `/api/telemetry` carries `visibility: {on_air: bool, agents: {codex, claude,
  hermes}}` - `on_air` is `GetSourceActive` on the Chryons scene (is it in the
  program path), `agents` is the enabled state of each chyron's scene item;
- `useTallyFeed` only sounds for an agent that is visible, and never when the
  scene is off air;
- the baseline keeps moving either way, so a chyron that comes back does not
  fire a burst of catch-up tallies for growth it accumulated while hidden;
- the event stream normally beats once a second, but it **pushes immediately
  when visibility changes** (it wakes every 100ms to check), so hiding the
  chyrons goes quiet in about a tenth of a second rather than up to a second.

`GET /api/health` shows the same thing as `reflow.visibility`. Measured in OBS:
a real Hermes increment grew the counters 201.6M → 202.3M while the Chryons
scene was off air and the tally source's play count stayed at 0
(`scripts/check_tally_gate.py` reproduces that check).

Two notes: refresh the browser sources after a rebuild (`scripts/obs_chyrons.py
--refresh`) or OBS keeps running the old bundle, and `--no-reflow` disables the
reflow service, which makes visibility unknown - unknown counts as *audible*, so
the tally never goes silent by accident.

## OBS wiring (already applied)

`scripts/obs_chyrons.py` created and configured everything below in the
hack.party collection; re-run it after changing the port, key colour or canvas
size and it updates in place (it is idempotent):

```bash
STREAMDECK_ROOT=/media/rspectre/Storage/workspace/streamdeck \
  /media/rspectre/Storage/workspace/streamdeck/.venv/bin/python scripts/obs_chyrons.py
# --list shows the current state, --remove takes the sources back out
```

| source | url | size | scene item |
| --- | --- | --- | --- |
| `Token Chyron - Codex` | `…:8790/?agent=codex&bg=green` | 3840x440 @ scale 0.5 → 1920x220 | x=0 y=40 |
| `Token Chyron - Claude` | `…:8790/?agent=claude&bg=green` | 3840x440 @ scale 0.5 → 1920x220 | x=0 y=268 |
| `Token Chyron - Hermes` | `…:8790/?agent=hermes&bg=green` | 3840x440 @ scale 0.5 → 1920x220 | x=0 y=496 |
| `Token Chyron - Tally` | `…:8790/?tally=1` | 64x64, off-canvas | x=-72 y=-72 |

All four live in a dedicated **Chryons** scene rather than sharing Scene Overlays,
which is what lets several chyrons be visible at once. Inside it they are
top-down rows 228px apart (220 canvas + 8px gap). Each banner has a **Chroma
Key** filter (`chroma_key_filter_v2`, green, similarity 400, smoothness 80,
spill 100) and keeps its browser running while hidden, so a chyron is warm and
up to date the moment you reveal it.

The Chryons scene is nested into every scene that used to contain Scene
Overlays - 14 of them - placed directly in front of it, so the chyrons reach the
stream exactly where they did before. Add or remove the Chryons scene like any
other source if you want them somewhere else.

`Token Chyron - Tally` is the audio: a 64x64 browser source parked off-canvas
that draws nothing (so it needs no chroma key), enabled so OBS keeps mixing its
audio, at 0.6 source volume on top of `?volume=`. Mute that source in the Audio
Mixer to silence the tally without touching the banners.

## Showing and hiding chyrons (top-down reflow)

OBS owns visibility; the server owns position. Toggle a chyron with the eye
icon, a deck key (an `item` control on the Chryons scene, like the soundboard
effect keys), or the websocket API - the visible ones stack from the top with no
gaps and hidden ones are parked off-canvas. Hiding Codex while Claude and Hermes
are up moves them to y=40 and y=268 within ~50ms (measured: it is event-driven,
with a 2s reconcile as a safety net).

That lives in `server/obs_reflow.py`, runs inside the chyron server by default
(`--no-reflow` turns it off) and reports itself in `/api/health` as `reflow`.

## Deck keys: page 1 columns 27-29

Crickets, Laughs and Sad on page 1 are chyron toggles now - Codex, Claude and
Hermes in that order. Each is an `item` live control on the Chryons scene, the
same mechanism as the camera keys on that page, so the key shows amber while the
chyron is up, dim while it is hidden and red when OBS cannot answer. Pressing it
enables/disables that chyron's scene item; the reflow service then restacks the
visible ones and the tally goes quiet for the one you hid.

The icons are drawn by `scripts/make_deck_icons.py` in the house style of the
existing deck art (144x144, rounded tile, inset frame, glyph in the state
colour): angle brackets for Codex, a sunburst for Claude, and the caduceus -
Hermes' own staff, and the emblem that reads as "surgeon" - for Hermes. Glyphs
come in three paint modes (`stroke`, `fill`, `both`) because the caduceus mixes
a stroked staff and snakes with solid wings, and each glyph is checked at 64px
as well as the XL's 96px key size before it ships. The earlier wing glyph is
still in the file: swap `CADUCEUS` for `WING` in `GLYPHS['hermes']`.

**The durable edit is the deck repo's layout spec**, not the installed profile:
`tools/build_opendeck.py` regenerates the profiles from `opendeck-layout.json`,
so anything written straight into `~/.config/opendeck` is lost the next time the
deck is installed. Both directions are available:

```bash
# the durable one: edit the spec, then regenerate and install from the deck repo
python3 scripts/wire_deck_chyrons.py --spec ../streamdeck/opendeck-layout.json
python3 scripts/make_deck_icons.py --controls ../streamdeck/images/controls
cd ../streamdeck && python3 tools/build_opendeck.py && .venv/bin/python -m unittest discover -s tests
python3 tools/install_opendeck.py     # OpenDeck must be quit first: it overwrites these files on exit

# the quick one: rewrite an installed profile in place, then restart OpenDeck
python3 scripts/wire_deck_chyrons.py --profile ~/.config/opendeck/profiles/<device>/01 - broadcast.json
```

Both are idempotent (they match the keys by the settings id they replace and
report "already wired" on a second run) and both back up the file they touch.

**After every `npm run build`, refresh the running sources** – OBS's embedded
browser caches the page and will otherwise keep rendering the previous bundle:

```bash
STREAMDECK_ROOT=/media/rspectre/Storage/workspace/streamdeck \
  /media/rspectre/Storage/workspace/streamdeck/.venv/bin/python scripts/obs_chyrons.py --refresh
```

They render at their native 4K size and are scaled to the canvas width, so if
you move the OBS canvas to 3840x2160 the banners are already 1:1 – set the scale
back to 1.0 and reposition (the script does this automatically, it reads the
canvas size).

Verify the wiring and grab screenshots of the scene:

```bash
STREAMDECK_ROOT=/media/rspectre/Storage/workspace/streamdeck \
  /media/rspectre/Storage/workspace/streamdeck/.venv/bin/python scripts/verify_obs_chyrons.py
# 38 checks; writes /tmp/obs-chyron-*.png and, when Chryons is in the program
# path, /tmp/chyron-stream-1080p.png + /tmp/chyron-stream-720p.png (the keyed
# overlay composited over a grey picture, at canvas size and at 720p). It
# toggles a chyron to measure the reflow, then puts it back.
```

OBS only renders sources that are in the program path, so source screenshots
come back empty (and the previews are skipped with a NOTE) when the scene you
have selected does not include Chryons.

Either point a Chroma Key *filter* at each source (as configured) or, if you
prefer keying a whole layer, add a Chroma Key filter on the scene/source above
them instead – the background is flat `#00ff00`, so OBS's default green key
settings work.

## Legibility (the design rule for this overlay)

Stream compression, not OBS, is the constraint, so the layout is deliberately
coarse: six capsules per banner, each with a short label and one value, plus the
turn state in the header. Nothing else.

| element | 4K source | on a 1080p canvas |
| --- | --- | --- |
| capsule value (`28.2M`) | 72px | 36px |
| capsule label (`SESS`) | 30px | 15px |
| model name (`GPT 6 ASTRA`) | 56px | 28px |
| brand (`CODEX`) | 50px | 25px |
| turn state (`ACTIVE`) | 44px | 22px |
| sparkline | 46px tall, 24 bars | 23px |
| context bar | 16px tall | 8px |
| gutter between capsules | 22px | 11px |

Labels are acronyms — `TOK/S`, `CTX`, `SESS`, `30D`, `MODEL`, `1H TOK` — and the
explanatory third line every capsule used to carry is gone (at 7px on canvas it
was the first thing a video encoder destroyed). Same for the per-agent source
line. Model names are compacted to fit (`claude-sonnet-4-5-20250929` →
`SONNET 4.5`, `deepseek-ai/DeepSeek-V4.1-Flash` → `DEEPSEEK V4.1`).

### Capsule widths

The row is a seven-unit grid that spans the card edge to edge, read left to
right as **MODEL, 1H TOK, TOK/S, CTX, SESS, 30D**. The model and the four numbers
take one unit each; the graph takes two so its bars stay visible, giving 505px
for a single capsule (253px on the canvas) and 1032px for the graph (516px), with
22px gutters and no trailing gap.

Bars are 33.8px wide with 7.4px gaps in the 4K source - 17px and 3.7px on the
canvas - and there are 24 of them, one per 2.5 minutes. The bar gap is a share of
the slot rather than a fixed width, so the bucket count can change without the
bars turning into slivers or slabs.

Columns are equal rather than content-hugging on purpose: the values tick while
you stream, so content-sized capsules would resize as "9.9" becomes "152.8M"
and the row would jitter. The harness asserts the 2:1 ratio, that no capsule is
narrower than 300px, and that nothing is clipped.

`tests/overlay-harness.mjs` asserts the sizes above, that the capsules fill the
row edge to edge, and that no text on the canvas drops below 14px, so this does
not silently regress.

## Layout

The whole surface scales with the browser source: one rem is 1/38.4 of the
viewport width (100px at 3840px), so the same build is correct at 4K, 1440p or
in a preview window. Below 1200px wide it falls back to normal 16px rems.

## Verifying

```bash
# Python: API contract, routing, path traversal and chroma-key pixel proof
.venv/bin/python -m unittest discover -s tests

# Browser: renders the banners, tracks the live feed, key switching, no console errors
google-chrome --headless=new --disable-gpu --no-first-run --window-size=3840,2160 \
  --user-data-dir=/tmp/chyrons-profile --remote-debugging-port=9333 \
  "http://127.0.0.1:8790/?bg=green" &
node tests/overlay-harness.mjs        # 64 checks, exits non-zero on failure
```

The pixel tests read `/tmp/chyron-4k-green.png` (written by the harness) and
prove the key colour is flat at the edges, that no key-coloured pixel survives
inside a banner, and that the panels stay opaque. Set `CHYRON_SHOT` to test a
different capture.

## Files

```
server/chyrons_server.py        telemetry API + static dist server (stdlib http.server)
server/agent_extras.py          model names + hourly token series (read-only)
server/obs_reflow.py            keeps the Chryons scene stacked from the top;
                                publishes on-air / per-chyron visibility
scripts/obs_chyrons.py          create/update the Chryons scene, sources, chroma key
scripts/verify_obs_chyrons.py   check the wiring, measure the reflow, screenshot
scripts/check_tally_gate.py     live: is the tally silent while off air?
scripts/make_deck_icons.py      draw the page-1 chyron toggle key art
scripts/wire_deck_chyrons.py    point those keys at the chyrons (spec or profile)
src/App.vue                     chroma surface, banner stack, key switching, tally wiring
src/components/AgentBanner.vue  one banner: brand, turn state, six capsules
src/components/MetricTile.vue   capsule: short label + value, bar or sparkline
src/components/CountUp.vue      rolls a token count up when it grows
src/components/Sparkline.vue    24-bucket inline SVG token graph
src/lib/format.ts               compact token strings, parsed and formatted
src/lib/tally.ts                WebAudio mechanical click, one per flap
src/lib/flaps.ts                the flap schedule: one plan for the drum and the sound
src/composables/useTallyFeed.ts clicks once per flap, skipping chyrons that are not on screen
src/components/ui/*             shadcn-vue primitives (card, badge, progress, separator)
src/composables/useTelemetry.ts SSE with polling fallback, coalesced server-side
src/lib/demo.ts                 offline sample data for ?demo=1
tests/                          Python unit + pixel tests, CDP browser harness
```

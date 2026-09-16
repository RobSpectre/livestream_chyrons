<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import AgentBanner from "@/components/AgentBanner.vue"
import { useTelemetry } from "@/composables/useTelemetry"
import { useTallyFeed } from "@/composables/useTallyFeed"
import { demoTelemetry } from "@/lib/demo"
import { formatCompact, parseCompact } from "@/lib/format"
import { tally } from "@/lib/tally"
import type { Banner, Telemetry } from "@/types"

/** Chroma presets for the OBS colour key. Green is the default key colour. */
const CHROMA: Record<string, string> = {
  green: "#00ff00",
  magenta: "#ff00ff",
  blue: "#0000ff",
  cyan: "#00ffff",
  transparent: "transparent",
}

const PLACEHOLDERS: Banner[] = [
  { key: "codex", label: "CODEX", accent: "#ffffff", tint: "#dcdcdc", ink: "#000000", source: "Codex CLI", metrics: {} },
  {
    key: "claude",
    label: "CLAUDE",
    accent: "#d97757",
    tint: "#f7e4dd",
    ink: "#000000",
    source: "Claude Code CLI",
    metrics: {},
  },
  {
    key: "hermes",
    label: "HERMES",
    accent: "#0000f2",
    tint: "#ccccfc",
    ink: "#ffffff",
    source: "Hermes agent CLI",
    metrics: {},
  },
]

const params = new URLSearchParams(window.location.search)
const demo = params.get("demo") === "1" || params.get("demo") === "true"
// ?agent=codex renders one banner alone, sized to fill its own OBS browser source.
const solo = params.get("agent")?.toLowerCase() ?? null
// ?tally=1 renders nothing and only plays the sound: a dedicated audio source.
const tallyOnly = params.get("tally") === "1"
const flip: Array<[string, string]> = [
  ["g", "green"],
  ["m", "magenta"],
  ["b", "blue"],
  ["t", "transparent"],
]

function resolveChroma(value: string | null): string {
  if (!value) return CHROMA.green
  return CHROMA[value.toLowerCase()] ?? (value.startsWith("#") ? value : CHROMA.green)
}

const { data, connected, push } = useTelemetry({
  demo,
  manual: params.get("feed") === "manual",
})
const chroma = ref(resolveChroma(params.get("bg")))
useTallyFeed(data)

// The tally is opt-in: with no parameter the banners stay silent.
tally.configure({
  enabled: params.get("sound") === "1" || tallyOnly,
  volume: params.has("volume") ? Number(params.get("volume")) : undefined,
  minGapMs: params.has("tallygap") ? Number(params.get("tallygap")) : undefined,
  // Named so the live service can tell the OBS audio source from a test browser.
  page: tallyOnly ? "audio-only" : "banner",
})

// Keep all three banners on screen even before the first telemetry frame lands.
const banners = computed<Banner[]>(() => {
  const live = data.value?.agents ?? []
  const fallback = demo ? demoTelemetry().agents : PLACEHOLDERS
  const rows = live.length ? live : fallback
  const ordered = PLACEHOLDERS.map((row) => rows.find((item) => item.key === row.key) ?? row)
  return solo ? ordered.filter((row) => row.key === solo) : ordered
})

const offline = computed(() => !demo && !connected.value)

onMounted(() => {
  window.addEventListener("keydown", (event) => {
    const match = flip.find(([key]) => key === event.key.toLowerCase())
    if (match) chroma.value = CHROMA[match[1]]
  })
  // Test surface for tests/overlay-harness.mjs: feed a frame, read the tally.
  Object.assign(window, {
    __chyron: {
      push: (payload: Telemetry) => push(payload),
      parseCompact: (text: string) => parseCompact(text),
      formatCompact: (value: number) => formatCompact(value),
      tally: {
        get plays() {
          return tally.plays
        },
        get rolls() {
          return tally.rolls
        },
        get gaps() {
          return [...tally.gaps]
        },
        get enabled() {
          return tally.enabled
        },
        get state() {
          return tally.state
        },
        get error() {
          return tally.error
        },
      },
    },
  })
})
</script>

<template>
  <!-- Audio-only mode: nothing is drawn, so the source needs no chroma key. -->
  <div v-if="tallyOnly" class="h-full w-full" style="background: transparent" data-role="silent" />
  <!-- Banner surfaces stay fully opaque: a translucent card would pick up the key colour and get keyed out. -->
  <div
    v-else
    class="h-full w-full"
    :class="
      solo
        ? 'flex items-start justify-center px-[0.6rem] pt-[0.1rem] pb-[0.32rem]'
        : 'p-[0.6rem]'
    "
    :style="{ backgroundColor: chroma }"
  >
    <div
      class="flex w-full flex-col"
      :class="solo ? 'gap-0' : 'h-full justify-center gap-[0.34rem]'"
    >
      <AgentBanner
        v-for="banner in banners"
        :key="banner.key"
        :banner="banner"
        :demo="demo"
        :offline="offline"
      />
    </div>
  </div>
</template>

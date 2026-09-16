<script setup lang="ts">
import { computed } from "vue"
import { Card } from "@/components/ui/card"
import MetricTile from "@/components/MetricTile.vue"
import { ROW_KEYS, type Banner, type CapsuleKey, type ExtraKey } from "@/types"
import { TONE_COLOUR, tonePulses, turnTone } from "@/lib/turnState"

const props = defineProps<{ banner: Banner; demo?: boolean; offline?: boolean }>()

const activity = computed(() => props.banner.metrics.activity?.value?.toUpperCase() ?? "—")
// Off-air states are the only ones worth spelling out; on-air the colour says it.
const status = computed(() => (props.demo ? "DEMO" : props.offline ? "NO SERVER" : activity.value))

// Green working, red wanting a human, grey otherwise - the chip's fill, with black
// ink on it. "NO SERVER" and "DEMO" read as grey, which is honest: neither is work.
const tone = computed(() => turnTone(props.demo ? "DEMO" : props.offline ? "no server" : activity.value))
const toneColour = computed(() => TONE_COLOUR[tone.value])
const pulsing = computed(() => tonePulses(tone.value))

// Quota is deliberately not rendered; the payload still carries it for the deck.
const capsule = (key: CapsuleKey | ExtraKey) =>
  key === "model" || key === "spark" ? props.banner.extras?.[key] : props.banner.metrics[key]

const capsules = computed(() => ROW_KEYS)
</script>

<template>
  <!-- Neobrutalist frame: black card, thick black border, hard offset black shadow,
       and flat blocks of the agent colour inside. No gradients, no soft anything.
       The frame is utilities, not a class: utilities beat the components layer. -->
  <Card
    :data-agent="banner.key"
    class="relative w-full shrink-0 overflow-hidden rounded-[var(--nb-radius)] border-[length:var(--nb-border-width)] border-black p-[0.2rem] shadow-[var(--nb-shadow-x)_var(--nb-shadow-y)_0_0_#000]"
    :style="{ backgroundColor: banner.tint }"
  >
    <!-- Header: who, then turn state, both as block-colour chips. -->
    <div class="flex items-center gap-[0.2rem] pb-[0.16rem]">
      <span
        data-role="brand"
        class="rounded-[var(--nb-radius-chip)] border-[length:var(--nb-border-chip-width)] border-black px-[0.26rem] py-[0.06rem] text-[0.5rem] font-black tracking-[0.04em] shadow-[var(--nb-shadow-chip-x)_var(--nb-shadow-chip-y)_0_0_#000]"
        :style="{ backgroundColor: banner.accent, color: banner.ink }"
        >{{ banner.label }}</span
      >
      <span
        data-role="state"
        :data-tone="tone"
        class="turn-tone ml-auto flex shrink-0 items-center gap-[0.14rem] rounded-[var(--nb-radius-chip)] border-[length:var(--nb-border-chip-width)] border-black px-[0.26rem] py-[0.08rem] text-[0.44rem] font-black tracking-[0.06em] shadow-[var(--nb-shadow-chip-x)_var(--nb-shadow-chip-y)_0_0_#000]"
        :class="pulsing && 'turn-pulse'"
        :style="{ backgroundColor: toneColour, color: 'var(--state-ink)' }"
      >
        <span
          class="h-[0.18rem] w-[0.18rem] shrink-0 rounded-full"
          :style="
            pulsing
              ? { backgroundColor: 'var(--state-ink)' }
              : { border: '0.03rem solid var(--state-ink)' }
          "
        />
        <span>{{ status }}</span>
      </span>
    </div>

    <!-- Seven units across, no trailing gap: the model and the hour graph lead,
         the graph spanning two units, then the four numbers at one unit each. -->
    <div class="grid grid-cols-7 gap-[0.22rem]" data-role="capsules">
      <MetricTile
        v-for="key in capsules"
        :key="key"
        :metric-key="key"
        :metric="capsule(key)"
        :accent="banner.accent"
        :ink="banner.ink"
        :size="key === 'model' ? 'md' : 'lg'"
        :class="key === 'spark' && 'col-span-2'"
      />
    </div>
  </Card>
</template>

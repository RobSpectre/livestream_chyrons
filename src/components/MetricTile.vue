<script setup lang="ts">
import { computed } from "vue"
import { Progress } from "@/components/ui/progress"
import CountUp from "@/components/CountUp.vue"
import Sparkline from "@/components/Sparkline.vue"
import { SHORT_LABELS, type ExtraKey, type Metric, type MetricKey } from "@/types"

const props = defineProps<{
  metricKey: MetricKey | ExtraKey
  metric?: Metric
  /** Block colour of the capsule: the agent accent. */
  accent: string
  /** Text colour that stays legible on that block. */
  ink: string
  /** Model names are longer than numbers, so they render one step smaller. */
  size?: "lg" | "md"
}>()

/** Context left is a percentage, so it earns a bar. */
const percent = computed(() => {
  const value = props.metric?.value
  if (!value) return null
  const match = /^(\d+(?:\.\d+)?)%$/.exec(value.trim())
  return match ? Number(match[1]) : null
})

const label = computed(() => SHORT_LABELS[props.metricKey])
const series = computed(() => props.metric?.series ?? null)
const valueSize = computed(() => (props.size === "md" ? "text-[0.56rem]" : "text-[0.72rem]"))
/** Only the two capsules that grow get the roll-up and the tally. */
const counts = computed(() => props.metricKey === "session" || props.metricKey === "month")
</script>

<template>
  <!-- Neobrutalist capsule: flat block colour, black frame, hard offset shadow.
       Two lines of text only - a third would not survive the video encoder. -->
  <div
    :data-metric="props.metricKey"
    class="flex min-w-0 flex-col justify-center gap-[0.1rem] rounded-[var(--nb-radius-chip)] border-[length:var(--nb-border-chip-width)] border-black px-[0.18rem] py-[0.16rem] shadow-[var(--nb-shadow-chip-x)_var(--nb-shadow-chip-y)_0_0_#000]"
    :style="{ backgroundColor: accent }"
  >
    <div
      data-role="label"
      class="truncate text-[0.3rem] font-extrabold tracking-[0.1em] opacity-70"
      :style="{ color: ink }"
    >
      {{ label }}
    </div>
    <div
      data-role="value"
      class="truncate font-extrabold leading-none"
      :class="valueSize"
      :style="{ color: ink }"
    >
      <CountUp v-if="counts" :text="metric?.value" />
      <template v-else>{{ metric?.value ?? "—" }}</template>
    </div>
    <Progress
      v-if="props.metricKey === 'context'"
      class="mt-[0.05rem] h-[0.16rem]"
      :model-value="percent"
      :accent="ink"
      :track="ink"
    />
    <Sparkline v-else-if="series" class="mt-[0.05rem]" :series="series" :accent="ink" />
  </div>
</template>

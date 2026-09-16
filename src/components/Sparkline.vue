<script setup lang="ts">
import { computed } from "vue"

const props = withDefaults(defineProps<{ series: number[]; accent: string; height?: number }>(), {
  // 0.46rem is 46px in the 4K source, 23px on the canvas: still readable after
  // a 720p downscale, unlike the 17px version this started as.
  height: 0.46,
})

/** Buckets, oldest first. Bars scale to the busiest bucket in the window. */
const bars = computed(() => {
  const series = props.series
  const peak = Math.max(...series, 0)
  const slot = 100 / Math.max(1, series.length)
  // The gap is a share of the slot, not a fixed width: 24 buckets have to stay
  // thin and 12 have to stay chunky with the same code.
  const gap = slot * 0.18
  return series.map((value, index) => {
    // A floor keeps empty buckets visible as a baseline instead of a gap.
    const ratio = Math.max(0.06, peak > 0 ? value / peak : 0)
    return {
      key: index,
      x: index * slot,
      width: Math.max(0.4, slot - gap),
      top: 100 - ratio * 100,
      height: ratio * 100,
      value,
      empty: value <= 0,
    }
  })
})

const label = computed(() => {
  const peak = Math.max(...props.series, 0)
  const total = props.series.reduce((sum, value) => sum + value, 0)
  return `${props.series.length} buckets, peak ${peak}, total ${total}`
})
</script>

<template>
  <!-- Inline SVG, no chart library: bars survive downscaling better than a line. -->
  <svg
    data-role="sparkline"
    class="w-full"
    :style="{ height: `${height}rem` }"
    viewBox="0 0 100 100"
    preserveAspectRatio="none"
    role="img"
    :aria-label="label"
  >
    <rect
      v-for="bar in bars"
      :key="bar.key"
      :x="bar.x"
      :y="bar.top"
      :width="bar.width"
      :height="bar.height"
      :fill="accent"
      :opacity="bar.empty ? 0.35 : 0.95"
      :data-bucket="bar.key"
      :data-value="bar.value"
    />
  </svg>
</template>

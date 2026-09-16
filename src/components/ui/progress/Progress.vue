<script setup lang="ts">
import type { HTMLAttributes } from "vue"
import { computed } from "vue"
import { cn } from "@/lib/utils"

const props = withDefaults(
  defineProps<{
    class?: HTMLAttributes["class"]
    /** Progress value, 0-100. Out-of-range values are visually clamped. */
    modelValue?: number | null
    /** Fill colour. */
    accent?: string
    /** Track colour, drawn at low opacity so it works on any block colour. */
    track?: string
  }>(),
  { modelValue: 0 },
)

const clamped = computed(() =>
  props.modelValue === null || props.modelValue === undefined
    ? null
    : Math.max(0, Math.min(100, props.modelValue)),
)
</script>

<template>
  <!-- Neobrutalist bar: square ends, black frame, flat fill. The track is the
       ink at 30% so the empty part stays visible on any block colour. -->
  <div
    role="progressbar"
    :aria-valuemin="0"
    :aria-valuemax="100"
    :aria-valuenow="clamped ?? undefined"
    :class="cn('relative h-2 w-full overflow-hidden border border-black', props.class)"
    :style="{ backgroundColor: track ? `${track}4d` : 'var(--muted)' }"
  >
    <div
      v-if="clamped !== null"
      class="h-full flex-1 transition-[width] duration-500 ease-out"
      :style="{ width: `${clamped}%`, backgroundColor: accent ?? 'var(--primary)' }"
    />
  </div>
</template>

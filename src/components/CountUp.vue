<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { formatCompact, parseCompact } from "@/lib/format"
import { planFlaps, type FlapStep } from "@/lib/flaps"

/**
 * A rail-station split-flap counter.
 *
 * The value advances in flaps of one display step - the smallest change that
 * alters the formatted string, so '209.1M' clatters through 0.1M at a time -
 * and every digit that changes rolls upwards: the old digit leaves through the
 * top while the new one rises from below, like the flap drums on a departure
 * board. The schedule comes from `@/lib/flaps`, which the tally sound follows
 * too, so the clicks land on the flaps.
 *
 * Two rules this must not break:
 *  - the settled text is character-for-character what the API sent (the compact
 *    strings are compared against the telemetry modules elsewhere);
 *  - the value only ever moves forward while rolling, so the displayed number
 *    never dips mid-animation.
 */
const props = defineProps<{
  /** The formatted value from the API, e.g. '33.3M' or '-' for a placeholder. */
  text?: string
  /** Longest a roll-up may take, in milliseconds. */
  duration?: number
}>()

const emit = defineEmits<{ (event: "increment"): void }>()

const parsed = computed(() => parseCompact(props.text))
const display = ref(parsed.value.value ?? 0)
/** Flaps in the roll-up currently on screen; 0 when settled. Exposed for tests. */
const flaps = ref(0)

/** Placeholders pass straight through: an animation must never alter them. */
const shown = computed(() =>
  parsed.value.value === null
    ? (props.text ?? "—")
    : parsed.value.prefix + formatCompact(display.value),
)

// -- the flap faces -----------------------------------------------------------
// Only a digit whose character changed is re-keyed, so untouched digits sit
// still. The current character is real text - the callers read textContent and
// compare it with the API string - while the outgoing character is a
// pseudo-element, which keeps it out of textContent and still lets one digit
// roll over another.
interface Cell {
  ch: string
  prev: string
  key: number
  /** How long this flap takes, in ms: short while rolling, longer when it lands. */
  ms: number
}

const cells = ref<Cell[]>([])
let nextKey = 0
let lastChanged = -1
/** Set just before a value changes, so the flap is timed with its step. */
let flapMs = 110
/** Timeouts for the roll in progress, so a new reading can replace it cleanly. */
let timers: number[] = []

function paint(next: string) {
  const prior = cells.value
  let changed = -1
  cells.value = next.split("").map((ch, index) => {
    const before = prior[index]
    if (before && before.ch === ch) return before
    changed = index
    return { ch, prev: before?.ch ?? ch, key: (nextKey += 1), ms: flapMs }
  })
  if (changed >= 0) lastChanged = changed
}

watch(shown, (next) => paint(next), { immediate: true })

// -- the roll-up --------------------------------------------------------------
function clear() {
  for (const timer of timers) window.clearTimeout(timer)
  timers = []
}

/**
 * A decorative flip from the plan: the drum moves, the number does not. The
 * outgoing digit is invented, which is safe because it is only ever drawn as a
 * pseudo-element and never as text.
 */
function reflip(step: FlapStep) {
  const index = lastChanged
  const cell = cells.value[index]
  if (!cell || !/\d/.test(cell.ch)) return
  cells.value[index] = {
    ch: cell.ch,
    prev: String((Number(cell.ch) + 1 + Math.floor(Math.random() * 4)) % 10),
    key: (nextKey += 1),
    ms: step.ms,
  }
}

watch(
  () => props.text,
  () => {
    const { value } = parsed.value
    clear()
    if (value === null) {
      display.value = 0
      flaps.value = 0
      return
    }
    const from = display.value
    // Snap on a reset (a new session starts at zero) rather than rolling back.
    if (value <= from) {
      display.value = value
      flaps.value = 0
      return
    }
    emit("increment")

    const { steps } = planFlaps({ from, to: value, text: props.text, budgetMs: props.duration ?? 900 })
    if (!steps.length) return
    flaps.value = steps.length
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    for (const step of steps) {
      timers.push(window.setTimeout(() => {
        flapMs = step.ms
        if (step.decorative) {
          if (!reduced) reflip(step)
          return
        }
        display.value = step.value
      }, step.at))
    }
    timers.push(window.setTimeout(() => { timers = [] }, steps[steps.length - 1].at + 200))
  },
)

onBeforeUnmount(clear)
</script>

<template>
  <span data-role="counter" class="tabular flap-counter" :data-flaps="flaps">
    <span
      v-for="cell in cells"
      :key="cell.key"
      class="flap"
      :data-prev="cell.prev"
      :style="{ '--flap-ms': `${cell.ms}ms` }"
    >
      <span class="flap-face">{{ cell.ch }}</span>
    </span>
  </span>
</template>

<style scoped>
.flap-counter {
  display: inline-flex;
}

/* Clipped drum: the incoming digit rises from below while the outgoing one
   leaves through the top. The padding keeps ascenders and descenders off the
   clip edge. */
.flap {
  position: relative;
  display: inline-block;
  overflow: hidden;
  padding: 0.06em 0;
  margin: -0.06em 0;
}

.flap-face {
  display: inline-block;
  animation: flap-in var(--flap-ms, 110ms) cubic-bezier(0.2, 0.75, 0.25, 1) both;
}

.flap::after {
  content: attr(data-prev);
  position: absolute;
  inset: 0.06em 0;
  text-align: center;
  animation: flap-out var(--flap-ms, 110ms) cubic-bezier(0.2, 0.75, 0.25, 1) both;
}

@keyframes flap-in {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

@keyframes flap-out {
  from {
    transform: translateY(0);
  }
  to {
    transform: translateY(-100%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .flap-face,
  .flap::after {
    animation: none;
  }

  .flap::after {
    content: none;
  }
}
</style>

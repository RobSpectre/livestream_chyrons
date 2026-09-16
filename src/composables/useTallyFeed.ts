import { watch, type Ref } from 'vue'
import { planFlaps } from '@/lib/flaps'
import { parseCompact } from '@/lib/format'
import { tally } from '@/lib/tally'
import type { Visibility, Telemetry } from '@/types'

const COUNTING = ['session', 'month'] as const

/**
 * Phase offset per counter, in milliseconds.
 *
 * Session and 30-day move by the same amount in the same frame - they count the
 * same tokens - so their plans are identical and every click would land on the
 * same millisecond, where the click floor throws half of them away. Two drums on
 * a real machine never land together; half a flap apart interleaves them into a
 * denser rattle instead of a doubled one.
 */
const PHASE_MS: Record<string, number> = { session: 0, month: 24 }

/**
 * Clicks once per flap of the counter drum.
 *
 * This lives on the data stream rather than in the capsule component, because
 * the audio-only source (?tally=1) renders no capsules at all - there is no drum
 * to watch, but the clicks still have to land on the beat it would have kept. So
 * the drum (`CountUp.vue`) and this run the same plan from `@/lib/flaps`: same
 * step size, same cadence, same landing flap.
 *
 * A chyron that is off screen is silent. Visibility is re-checked as each click
 * fires, so a scene switch or a key press mid-roll silences the rest of that
 * roll instead of letting it click on from a stale reading. Baseline tracking
 * keeps running either way, so a chyron that comes back does not fire a burst of
 * catch-up clicks for growth it accumulated while hidden.
 */
export function useTallyFeed(source: Ref<Telemetry | null | undefined>) {
  const seen = new Map<string, number>()
  const timers = new Map<string, number[]>()
  /** Read again at click time, so a mid-roll change of scene takes effect. */
  let visibility: Visibility | undefined

  const clear = (id: string) => {
    for (const timer of timers.get(id) ?? []) window.clearTimeout(timer)
    timers.delete(id)
  }

  watch(source, (payload) => {
    visibility = payload?.visibility
    for (const agent of payload?.agents ?? []) {
      const audible = () =>
        visibility ? visibility.on_air && visibility.agents?.[agent.key] !== false : true
      for (const key of COUNTING) {
        const text = agent.metrics?.[key]?.value
        const { value } = parseCompact(text)
        if (value === null) continue
        const id = `${agent.key}.${key}`
        const previous = seen.get(id)
        seen.set(id, value)
        // The first sighting is a baseline, not an increment.
        if (previous === undefined) continue
        clear(id)
        if (value <= previous) continue
        const { steps } = planFlaps({ from: previous, to: value, text })
        const phase = PHASE_MS[key] ?? 0
        timers.set(id, steps.map((step) => window.setTimeout(() => {
          if (!audible()) return
          // A click on the way up is lighter than the one that lands, and a
          // decorative settle flip sits between the two.
          const strength = step.decorative ? 0.7 : step.value < value ? 0.55 : 1
          tally.flap(strength)
        }, step.at + phase)))
      }
    }
  })
}

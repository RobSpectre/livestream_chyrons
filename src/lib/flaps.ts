/**
 * The flap schedule of the split-flap counter, shared by the drum and the sound.
 *
 * Both consumers - `CountUp.vue`, which rolls the digits, and `useTallyFeed`,
 * which clicks once per flap - run this same plan, so the tally stays in step
 * with the picture. It has to live here rather than in the component because the
 * audio-only OBS source renders no capsules at all: no drum, but the clicks
 * still have to land on the same beat.
 */

export const TICK_MS = 48
/** A mid-roll flap must land inside its tick, or the drum reads as mush. */
export const FLAP_MS = TICK_MS - 6
/** The last flap is allowed to take longer: that is the drum settling. */
export const SETTLE_MS = 110
/** A huge jump has to be truncated, or the number is still spinning when the next reading lands. */
export const MAX_FLAPS = 16
/** Extra decorative flips after a short roll, so a small update still rattles. */
export const FLUTTER = 2
export const FLUTTER_MS = 74
/** Mechanical irregularity: a metronome reads as smooth counting, not machinery. */
const JITTER = [0, -7, 5, -4, 7, -2, 4, -6]

export interface FlapStep {
  /** The value shown once this flap lands. */
  value: number
  /** Milliseconds after the roll starts that this flap begins. */
  at: number
  /** How long the flap takes. */
  ms: number
  /** A decorative flip: the drum moves, the number does not change. */
  decorative?: boolean
}

export interface FlapPlan {
  steps: FlapStep[]
  /** Total length of the roll, in milliseconds. */
  duration: number
}

/** The smallest step that changes the text: '209.1M' flaps in 100000s, '96K' in 100s. */
export function displayStep(text: string | undefined, fallback: number): number {
  const mantissa = (text ?? "").replace(/[^0-9.]/g, "")
  const decimals = mantissa.includes(".") ? mantissa.split(".")[1].length : 0
  const suffix = /([KMB])$/.exec((text ?? "").trim())?.[1]
  const scale = suffix === "B" ? 1e9 : suffix === "M" ? 1e6 : suffix === "K" ? 1e3 : 1
  return (10 ** -decimals) * scale || fallback
}

/**
 * The flaps a roll-up makes, in order. Empty when the value does not grow.
 *
 * `text` is the formatted target: the flap size is derived from it so the drum
 * steps exactly one displayed digit at a time.
 */
export function planFlaps(options: {
  from: number
  to: number
  text?: string
  budgetMs?: number
}): FlapPlan {
  const { from, to, text, budgetMs = 900 } = options
  if (!(to > from)) return { steps: [], duration: 0 }

  const span = to - from
  const step = displayStep(text, span / MAX_FLAPS)
  const total = Math.max(1, Math.round(span / step))
  let perFlap = Math.max(1, Math.ceil(total / MAX_FLAPS))
  // Never overrun the budget, even when the jump is enormous.
  if (Math.ceil(total / perFlap) * TICK_MS > budgetMs) {
    perFlap = Math.max(perFlap, Math.ceil(total / Math.max(1, Math.floor(budgetMs / TICK_MS))))
  }

  const steps: FlapStep[] = []
  const flapCount = Math.ceil(total / perFlap)
  let advanced = 0
  let count = 0
  let at = 0
  while (steps.length < flapCount) {
    advanced += perFlap
    count += 1
    at += TICK_MS + JITTER[count % JITTER.length]
    const value = Math.min(to, from + advanced * step)
    steps.push({ value, at, ms: value < to ? FLAP_MS : SETTLE_MS })
    if (value >= to) break
  }

  // A one- or two-flap roll is over too fast to read as machinery, so it gets a
  // couple of extra decorative flips. They click too: the drum really moved.
  if (steps.length <= FLUTTER + 1) {
    for (let remaining = FLUTTER; remaining > 0; remaining -= 1) {
      at += FLUTTER_MS
      steps.push({ value: to, at, ms: FLUTTER_MS, decorative: true })
    }
  }

  const last = steps[steps.length - 1]
  return { steps, duration: last ? last.at + last.ms : 0 }
}

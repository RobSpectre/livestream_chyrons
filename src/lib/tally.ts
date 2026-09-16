/**
 * Mechanical counter tally, synthesised in the browser - no audio asset to ship.
 *
 * One click per flap of the counter drum, so a roll-up rattles like a tally
 * clicker instead of firing a single ka-ching per update. Each click is a short
 * band-passed noise burst plus a high partial, detuned slightly at random (a
 * clockwork rattle is never perfectly in tune) and held well under a
 * millisecond-scale envelope so a burst does not smear into mush.
 *
 * `minGapMs` is the floor between clicks: it is lower than the flap cadence, so
 * in normal use it never interferes, but it stops several agents' rolls landing
 * on the same beat from stacking into one louder transient.
 */

const DEFAULT_VOLUME = 0.14
/**
 * Floor between clicks. Only a guard against two rolls landing on the same
 * millisecond: it has to stay below the half-flap phase offset (24ms) between
 * an agent's two counters, or de-phasing them would not help.
 */
const DEFAULT_MIN_GAP_MS = 12

interface Options {
  enabled?: boolean
  volume?: number
  /** Minimum milliseconds between clicks. */
  minGapMs?: number
  /** Which page this is, so a test browser cannot impersonate the OBS source. */
  page?: string
}

class Tally {
  enabled = false
  volume = DEFAULT_VOLUME
  minGapMs = DEFAULT_MIN_GAP_MS
  /** Clicks played; the harness asserts on this. */
  plays = 0
  /** Roll-ups that produced at least one click. */
  rolls = 0
  /** Intervals between the last few clicks, so cadence can be measured. */
  gaps: number[] = []
  /** 'none' until the first click, then the AudioContext state ('running' | 'suspended'). */
  state = 'none'
  /** Last error from the audio path, if any, so a silent failure is visible. */
  error: string | null = null
  /** Reported with every state post, so /api/health can name the source. */
  page = 'overlay'

  private context: AudioContext | null = null
  private lastPlay = 0

  configure(options: Options) {
    if (options.enabled !== undefined) this.enabled = options.enabled
    if (options.volume !== undefined) this.volume = Math.max(0, Math.min(1, options.volume))
    if (options.minGapMs !== undefined) this.minGapMs = Math.max(0, Math.min(60000, options.minGapMs))
    if (options.page !== undefined) this.page = options.page
    // Report once as soon as we are enabled: this is how the OBS browser's
    // autoplay policy gets measured rather than assumed.
    if (this.enabled) this.hello()
  }

  /**
   * Open the audio graph and report the state *after* the resume attempt.
   * Reading it straight after construction would always say 'suspended', and
   * whether OBS lets it reach 'running' is the whole autoplay question.
   */
  async hello() {
    let context: AudioContext | null = null
    try {
      context = this.audio()
      if (context.state === 'suspended') await context.resume()
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : String(cause)
    }
    if (context) this.state = context.state
    this.report()
  }

  /**
   * One flap of the drum. `strength` shapes the click: 0.55 for a step on the
   * way up, 1 for the one that lands, 0.7 for a decorative settle flip.
   */
  flap(strength = 1, now = performance.now()) {
    if (!this.enabled) return false
    if (now - this.lastPlay < this.minGapMs) return false
    // A click after a pause is the start of a fresh roll-up, not a continuation.
    if (now - this.lastPlay > 400) this.rolls += 1
    if (this.lastPlay > 0) this.gaps = [...this.gaps.slice(-9), Math.round(now - this.lastPlay)]
    this.lastPlay = now
    this.plays += 1
    try {
      this.click(strength)
    } catch (cause) {
      this.error = cause instanceof Error ? cause.message : String(cause)
    }
    this.report()
    return true
  }

  /**
   * Tell the server what the audio graph is doing. OBS's embedded browser is
   * the only way to know whether playback is actually allowed there, so this is
   * how "the sound works on stream" gets checked instead of assumed.
   */
  private report() {
    const body = JSON.stringify({
      state: this.state,
      plays: this.plays,
      rolls: this.rolls,
      gap: this.gaps[this.gaps.length - 1] ?? null,
      error: this.error,
      volume: this.volume,
      page: this.page,
      agent: navigator.userAgent.slice(0, 60),
    })
    void fetch('/api/tally-state', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => {
      /* the report is a nicety; audio must not depend on it */
    })
  }

  private audio(): AudioContext {
    if (!this.context) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      this.context = new Ctor()
    }
    // OBS browser sources allow playback, but a fresh context can still start
    // suspended; resuming is harmless when it is already running.
    if (this.context.state === 'suspended') void this.context.resume()
    return this.context
  }

  private click(strength: number) {
    const context = this.audio()
    this.state = context.state
    const at = context.currentTime
    const level = Math.max(0, Math.min(1.5, strength))
    const master = context.createGain()
    master.gain.setValueAtTime(this.volume * level, at)
    master.connect(context.destination)

    // The mechanical part: 9ms of band-passed noise, swept down a little so it
    // reads as a click landing rather than a hiss.
    const samples = Math.floor(context.sampleRate * 0.009)
    const buffer = context.createBuffer(1, samples, context.sampleRate)
    const data = buffer.getChannelData(0)
    for (let i = 0; i < samples; i += 1) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / samples, 2.4)
    }
    const noise = context.createBufferSource()
    noise.buffer = buffer
    const band = context.createBiquadFilter()
    band.type = 'bandpass'
    // A few hundred Hz of scatter either way: a rack of identical clicks sounds synthetic.
    band.frequency.setValueAtTime(2300 + Math.random() * 700, at)
    band.Q.setValueAtTime(1.4, at)
    noise.connect(band).connect(master)
    noise.start(at)

    // The tick body: one short high partial, detuned per click.
    const frequency = (1980 + Math.random() * 420) * (strength >= 1 ? 1.12 : 1)
    const osc = context.createOscillator()
    const gain = context.createGain()
    osc.type = 'triangle'
    osc.frequency.setValueAtTime(frequency, at)
    osc.frequency.exponentialRampToValueAtTime(frequency * 0.92, at + 0.07)
    gain.gain.setValueAtTime(0, at)
    gain.gain.linearRampToValueAtTime(0.5, at + 0.003)
    gain.gain.exponentialRampToValueAtTime(0.0001, at + (strength >= 1 ? 0.13 : 0.07))
    osc.connect(gain).connect(master)
    osc.start(at)
    osc.stop(at + 0.2)
  }
}

export const tally = new Tally()
export type { Tally }

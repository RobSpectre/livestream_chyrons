/**
 * Turn state -> tone, and the block colour that tone draws with.
 *
 * The telemetry vocabulary is `ACTIVE`, `WAITING`, `IDLE`, `OFFLINE` and `OPEN`,
 * and there is no failure token in it yet, so this classifies by intent rather
 * than by exact match: anything that reads as "working" is purple, anything that
 * reads as "wants a human" - a pending approval, a prompt, a failure - is red,
 * and everything else (idle, offline, a CLI open with no session, a placeholder,
 * a state we have never seen) is grey. An unknown state falling back to grey is
 * deliberate: a wrong purple would claim work that is not happening.
 */
export type TurnTone = 'active' | 'idle' | 'attention'

/** Working. Leading \b keeps `INACTIVE` from reading as active. */
const ACTIVE = /\b(ACTIVE|WORKING|RUNNING|BUSY|THINKING|RESPONDING|STREAMING)/
/** Wants a human: waiting on input or permission, or something broke. */
const ATTENTION = /\b(WAIT|INPUT|APPROV|ATTENT|CONFIRM|PERMISSION|ERROR|FAIL|CRASH|BROKEN|PANIC)/

export function turnTone(state: string | null | undefined): TurnTone {
  const value = (state ?? '').trim().toUpperCase()
  if (!value || value === '—') return 'idle'
  if (ATTENTION.test(value)) return 'attention'
  if (ACTIVE.test(value)) return 'active'
  return 'idle'
}

/** Read back by the tests, which resolve them to real pixels. */
export const TONE_COLOUR: Record<TurnTone, string> = {
  active: 'var(--state-active)',
  idle: 'var(--state-idle)',
  attention: 'var(--state-attention)',
}

/** Purple and red pulse; grey sits still, because nothing is happening. */
export function tonePulses(tone: TurnTone): boolean {
  return tone !== 'idle'
}

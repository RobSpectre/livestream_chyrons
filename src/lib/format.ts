/** Parsing and formatting token counts exactly like the telemetry modules do. */

export interface Parsed {
  /** Numeric value, or null when the capsule shows a placeholder. */
  value: number | null
  /** Any marker the payload put in front, e.g. the "≥" on a lower bound. */
  prefix: string
}

/** '≥12.3M' -> { value: 12300000, prefix: '≥' }; '—' -> { value: null, prefix: '—' }. */
export function parseCompact(text: string | undefined | null): Parsed {
  if (!text) return { value: null, prefix: '—' }
  const match = /^(\D*?)(\d+(?:\.\d+)?)\s*([KMB])?$/.exec(text.trim())
  if (!match) return { value: null, prefix: text.trim() }
  const [, prefix, digits, suffix] = match
  const scale = suffix === 'B' ? 1e9 : suffix === 'M' ? 1e6 : suffix === 'K' ? 1e3 : 1
  return { value: Number(digits) * scale, prefix }
}

/** The same compacting the telemetry modules use, so animated text matches theirs. */
export function formatCompact(value: number): string {
  for (const [divisor, suffix] of [
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ] as const) {
    if (value >= divisor) return `${(value / divisor).toFixed(1)}${suffix}`
  }
  // int() truncates in the Python modules, so truncate here too.
  return String(Math.trunc(value))
}

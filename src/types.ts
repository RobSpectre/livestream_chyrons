/** Metric keys mirror the OpenDeck Page 6 telemetry buttons, in key order. */
export const METRIC_KEYS = ['activity', 'speed', 'context', 'session', 'month', 'quota'] as const

/** Capsule order in a banner: the two overlay-only capsules lead, then the numbers. */
export const EXTRA_KEYS = ['model', 'spark'] as const

/** Capsule order in a banner: the four numbers kept from the deck. */
export const CAPSULE_KEYS = ['speed', 'context', 'session', 'month'] as const

/** Reading order of the row: model, hour graph, then the four numbers. */
export const ROW_KEYS = [...EXTRA_KEYS, ...CAPSULE_KEYS] as const

export type MetricKey = (typeof METRIC_KEYS)[number]
export type CapsuleKey = (typeof CAPSULE_KEYS)[number]
export type ExtraKey = (typeof EXTRA_KEYS)[number]

/** Acronyms, not sentences: these have to read at 1080p through a video encoder. */
export const SHORT_LABELS: Record<MetricKey | ExtraKey, string> = {
  activity: 'TURN',
  speed: 'TOK/S',
  context: 'CTX',
  session: 'SESS',
  month: '30D',
  quota: 'QUOTA',
  model: 'MODEL',
  spark: '1H TOK',
}

export interface Metric {
  title: string
  value: string
  detail: string
  color: string
  /** Twenty-four 2.5-minute token buckets for the sparkline capsule. */
  series?: number[]
}

export interface Banner {
  key: string
  label: string
  /** Block colour of the banner's capsules and chips. */
  accent: string
  /** Light card the blocks sit on, so black frames and shadows read. */
  tint: string
  /** Text colour that stays legible on that block (black on white/orange, white on blue). */
  ink: string
  source: string
  metrics: Partial<Record<MetricKey, Metric>>
  extras?: Partial<Record<ExtraKey, Metric>>
}

export interface Visibility {
  /** True when the Chryons scene is in the OBS program path. */
  on_air: boolean
  /** Per chyron: is its scene item enabled? */
  agents: Record<string, boolean>
}

export interface Telemetry {
  generated: number
  ready?: boolean
  error?: string | null
  cached?: boolean
  agents: Banner[]
  /** Absent when the overlay runs without the reflow service. */
  visibility?: Visibility
}

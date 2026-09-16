import type { Banner, Metric, Telemetry } from '@/types'

/** Off-air sample data so the scene can be framed before any agent is running. */
interface Demo {
  activity: string
  activityDetail: string
  speed?: string
  context?: string
  session?: string
  month?: string
  model: string
  /** Tokens consumed per 2.5-minute bucket over the last hour. */
  series: number[]
}

function demoMetrics(brand: string, accent: string, sample: Demo): Record<string, Metric> {
  const row = (title: string, value: string, detail: string): Metric => ({
    title,
    value,
    detail,
    color: accent,
  })
  const codex = brand === 'CODEX'
  const total = sample.series.reduce((sum, value) => sum + value, 0)
  const compact = (value: number) =>
    value >= 1e6 ? `${(value / 1e6).toFixed(1)}M` : value >= 1e3 ? `${(value / 1e3).toFixed(1)}K` : String(value)
  return {
    activity: row('TURN', sample.activity, sample.activityDetail),
    speed: row('TOKENS / SEC', sample.speed ?? '—', codex ? 'TURN AVG · OUTPUT' : `${brand} · OBSERVED AVG`),
    context: row('CONTEXT LEFT', sample.context ?? '—', codex ? 'CURRENT TASK' : `${brand} · LAST REQUEST`),
    session: row('SESSION TOKENS', sample.session ?? '—', codex ? 'CURRENT TASK' : `${brand} · LATEST LOCAL`),
    month: row('30-DAY TOKENS', sample.month ?? '—', codex ? 'THIS COMPUTER' : `${brand} · LOCAL`),
    model: row('MODEL', sample.model, ''),
    spark: { ...row('1H TOK', compact(total), ''), series: sample.series },
  }
}

export function demoTelemetry(now = Date.now()): Telemetry {
  const phase = now / 1000
  const speed = 38 + 22 * Math.sin(phase / 7)
  const context = 74 - ((phase / 3) % 30)
  const wave = (offset: number) =>
    Array.from({ length: 24 }, (_, index) =>
      Math.round(60000 * (0.25 + 0.75 * Math.abs(Math.sin((index + offset) / 3.1)))),
    )

  const agents: Banner[] = [
    {
      key: 'codex',
      label: 'CODEX',
      accent: '#ffffff',
      tint: '#dcdcdc',
      ink: '#000000',
      source: 'demo · Codex CLI',
      metrics: demoMetrics('CODEX', '#ffffff', {
        activity: 'ACTIVE',
        activityDetail: 'LOCAL CODEX',
        speed: speed.toFixed(1),
        context: `${context.toFixed(0)}%`,
        session: '184.6K',
        month: '41.6M',
        model: 'GPT 5.6 LUNA',
        series: wave(0),
      }),
    },
    {
      key: 'claude',
      label: 'CLAUDE',
      accent: '#d97757',
      tint: '#f7e4dd',
      ink: '#000000',
      source: 'demo · Claude Code',
      metrics: demoMetrics('CLAUDE', '#d97757', {
        activity: 'IDLE',
        activityDetail: 'CLAUDE CODE CLI',
        context: '81%',
        session: '96.3K',
        month: '18.9M',
        model: 'OPUS 5',
        series: wave(1.7),
      }),
    },
    {
      key: 'hermes',
      label: 'HERMES',
      accent: '#0000f2',
      tint: '#ccccfc',
      ink: '#ffffff',
      source: 'demo · Hermes agent',
      metrics: demoMetrics('HERMES', '#0000f2', {
        activity: 'ACTIVE',
        activityDetail: 'HERMES AGENT CLI',
        speed: (speed * 0.8).toFixed(1),
        context: '62%',
        session: '212.4K',
        month: '27.3M',
        model: 'DEEPSEEK V4.1',
        series: wave(3.1),
      }),
    },
  ]

  // Demo rows keep the shape the live server sends: deck metrics plus extras.
  return {
    generated: now / 1000,
    agents: agents.map(({ metrics, ...rest }) => {
      const { model, spark, ...deck } = metrics as Record<string, Metric>
      return { ...rest, metrics: deck, extras: { model, spark } }
    }),
  }
}

import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { Telemetry } from '@/types'
import { demoTelemetry } from '@/lib/demo'

const ENDPOINT = '/api/telemetry'
const STREAM = '/api/stream'

export function useTelemetry(
  options: { demo?: boolean; intervalMs?: number; manual?: boolean } = {},
) {
  const intervalMs = options.intervalMs ?? 1000
  const demo = options.demo ?? false
  // ?feed=manual stops the stream and the poller: frames then only arrive
  // through push(), which is what makes the counter and tally testable.
  const manual = options.manual ?? false
  const data = ref<Telemetry | null>(null)
  const connected = ref(false)
  const error = ref<string | null>(null)
  const receivedAt = ref(0)

  let timer: number | undefined
  let source: EventSource | undefined
  let stopped = false

  const accept = (value: Telemetry) => {
    data.value = value
    connected.value = true
    error.value = value.error ?? null
    receivedAt.value = Date.now()
  }

  const pollOnce = async () => {
    try {
      const response = await fetch(ENDPOINT, { cache: 'no-store' })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      accept(await response.json())
    } catch (cause) {
      connected.value = false
      error.value = cause instanceof Error ? cause.message : 'telemetry unavailable'
    }
  }

  const startPolling = () => {
    if (timer !== undefined) return
    pollOnce()
    timer = window.setInterval(pollOnce, intervalMs)
  }

  const startStream = () => {
    try {
      source = new EventSource(STREAM)
    } catch {
      startPolling()
      return
    }
    source.onmessage = (event) => {
      try {
        accept(JSON.parse(event.data) as Telemetry)
      } catch {
        /* keep the last good frame */
      }
    }
    // Browser source or server restart: drop back to polling, which is always available.
    source.onerror = () => {
      source?.close()
      source = undefined
      connected.value = false
      startPolling()
    }
  }

  onMounted(() => {
    if (manual) return
    if (demo) {
      const tick = () => accept(demoTelemetry())
      tick()
      timer = window.setInterval(tick, intervalMs)
      return
    }
    startStream()
  })

  onBeforeUnmount(() => {
    stopped = true
    source?.close()
    if (timer !== undefined) window.clearInterval(timer)
  })

  return { data, connected, error, receivedAt, stopped, push: accept }
}

// WebSocket 实时事件：WS /ws/events → window.__poir_event（Pinia store 在
// bootstrap 时挂上处理函数）。断线指数退避重连；mock 传输（纯前端开发）不连接。
import { detectTransport } from './bridge'

let started = false

export function initRealtime(): void {
  if (started) return
  started = true
  void detectTransport().then((transport) => {
    if (transport === 'http') connect(0)
  })
}

function connect(attempt: number): void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/events`)
  ws.onmessage = (message) => {
    try {
      const data = JSON.parse(String(message.data)) as { type?: string; payload?: unknown }
      if (data?.type) window.__poir_event?.(data as never)
    } catch {
      /* 忽略无法解析的帧 */
    }
  }
  ws.onclose = () => {
    const delay = Math.min(1000 * 2 ** attempt, 10000)
    window.setTimeout(() => connect(Math.min(attempt + 1, 4)), delay)
  }
  ws.onerror = () => ws.close()
}

// 与 Python 桥接层共享的类型契约（src/webui/serialize.py 保持一致）

export interface TaskOptions {
  max_concurrency: number
  page_timeout_seconds: number
  max_retries: number
  screenshot_format: 'jpeg' | 'png'
  headless: boolean
}

export interface SheetStat {
  name: string
  done: number
  total: number
}

export interface SessionOverview {
  job_dir: string
  done: number
  total: number
  sheets: SheetStat[]
}

export interface Bootstrap {
  options: TaskOptions
  has_checkpoint: boolean
  session: SessionOverview | null
}

export interface ProgressPayload {
  completed: number
  total: number
  ready: number
  needs_review: number
  failed: number
  cancelled: number
  current_url: string
  stage: string
  percent: number
}

export interface LogPayload {
  time: string
  level: string
  message: string
  evidence_id: number | null
}

export interface JobStartedPayload {
  job_id: string
  label: string
  total: number
  rejected_count: number
}

export interface JobFinishedPayload {
  job_id: string
  label: string
  archive_path: string | null
  cancelled: boolean
  ready: number
  needs_review: number
  failed: number
  cancelled_count: number
  retryable: number
}

export interface SheetColumn {
  key: string // 模板列字母 A/B/C…
  header: string
  field: string | null // 可编辑字段名（OVERRIDEABLE_FIELDS），截图/附件列特殊标记
  editable: boolean
  required: boolean
  multiline: boolean
  choices: string[]
  kind: 'text' | 'url' | 'screenshot' | 'attachment'
}

export interface SheetRow {
  eid: number
  cells: Record<string, string> // 列字母 -> 展示值
  status: string
  status_text: string
  attention: boolean
  missing: string[]
  manual: boolean
  url: string
  final_url: string
}

export interface SheetPayload {
  name: string
  columns: SheetColumn[]
  rows: SheetRow[]
  manual_row_allowed: boolean
}

export interface AuthPlatform {
  key: string
  name: string
  status: string
  status_text: string
  tone: 'ok' | 'warn' | 'err' | 'muted'
  message: string
  account: string
}

export interface HistoryJob {
  path: string
  name: string
  modified: string
  records: number
}

export interface InputFileInfo {
  path: string
  url_count: number
  rejected_count: number
}

export type BridgeEventType =
  | 'started'
  | 'progress'
  | 'log'
  | 'session'
  | 'finished'
  | 'failed'
  | 'auth'

export interface BridgeEvent {
  type: BridgeEventType
  payload: unknown
}

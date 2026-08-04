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

export type LicenseStatus =
  | 'valid'
  | 'not_activated'
  | 'malformed'
  | 'bad_signature'
  | 'machine_mismatch'
  | 'expired'
  | 'fingerprint_error'

export interface LicenseInfo {
  activated: boolean
  status: LicenseStatus
  message: string
  machine_code: string
  licensee: string | null
  license_id: string | null
  expires_at: string | null
  ok: boolean
}

export interface Bootstrap {
  options: TaskOptions
  has_checkpoint: boolean
  session: SessionOverview | null
  license: LicenseInfo
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
  relevant: boolean
}

export interface InputFileInfo {
  path: string
  url_count: number
  rejected_count: number
}

export interface ScreenshotInfo {
  data_url: string
  name: string
}

export interface ScreenshotPair {
  content: ScreenshotInfo | null
  author: ScreenshotInfo | null
}

export interface CaptureEventPayload {
  eid: number
  target: 'content' | 'author'
  status: 'saved' | 'cancelled' | 'error'
  name: string
  message: string
}

export type BridgeEventType =
  | 'started'
  | 'progress'
  | 'log'
  | 'session'
  | 'finished'
  | 'failed'
  | 'auth'
  | 'auth_relogin'
  | 'capture'

export interface AuthReloginPayload {
  key: string
  name: string
  phase: 'waiting' | 'failed' | 'done'
  message: string
}

export interface BridgeEvent {
  type: BridgeEventType
  payload: unknown
}

// B/S 传输层：默认同源 HTTP（POST /api/{method}；事件走 WS /ws/events，见 events.ts）。
// 连不上后端时回退本地 mock，方便 `npm run dev` 纯前端开发。
import type {
  AuthPlatform,
  Bootstrap,
  BridgeEvent,
  InputFileInfo,
  LicenseInfo,
  LlmSavePayload,
  LlmSettingsPayload,
  ManualEntryRow,
  ScreenshotPair,
  SheetPayload,
  TaskOptions,
  UrlRecheckRow,
  InvalidUrlCandidateRow,
} from '@/types'

declare global {
  interface Window {
    __poir_event?: (event: BridgeEvent) => void
  }
}

type Transport = 'http' | 'mock'
let transportPromise: Promise<Transport> | null = null

/** 探测后端：同源请求一个轻量接口，失败即回退 mock（结果进程级缓存）。 */
export function detectTransport(): Promise<Transport> {
  if (!transportPromise) {
    transportPromise = (async (): Promise<Transport> => {
      try {
        const resp = await fetch('/api/letter_state', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{"args":[]}',
          signal: AbortSignal.timeout(3000),
        })
        return resp.ok ? 'http' : 'mock'
      } catch {
        return 'mock'
      }
    })()
  }
  return transportPromise
}

async function errorMessage(resp: Response, fallback: string): Promise<string> {
  try {
    const body = (await resp.json()) as { message?: string }
    if (body?.message) return body.message
  } catch {
    /* 非 JSON 错误页 */
  }
  return fallback
}

async function httpCall<T>(method: string, args: unknown[]): Promise<T> {
  const resp = await fetch(`/api/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ args }),
  })
  if (!resp.ok) {
    throw new Error(await errorMessage(resp, `接口 ${method} 失败（${resp.status}）`))
  }
  return (await resp.json()) as T
}

/** multipart 上传；field 为 FastAPI 的表单字段名（单文件 file / 多文件 files）。 */
async function uploadFiles<T>(
  path: string,
  field: string,
  files: File[],
  params?: Record<string, string>,
): Promise<T> {
  const form = new FormData()
  for (const file of files) form.append(field, file, file.name)
  const qs = params ? `?${new URLSearchParams(params).toString()}` : ''
  const resp = await fetch(`${path}${qs}`, { method: 'POST', body: form })
  if (!resp.ok) throw new Error(await errorMessage(resp, `上传失败（${resp.status}）`))
  return (await resp.json()) as T
}

async function call<T>(method: string, ...args: unknown[]): Promise<T> {
  const result =
    (await detectTransport()) === 'http'
      ? await httpCall<T>(method, args)
      : await mockCall<T>(method as string, ...args)
  // 业务方法被许可证守卫拦截时广播事件，由 store 刷新授权状态并切到激活页
  if (result && typeof result === 'object' && (result as { code?: string }).code === 'LICENSE_REQUIRED') {
    window.dispatchEvent(new CustomEvent('poir-license-required'))
  }
  return result
}

// ── 浏览器 dev mock ────────────────────────────────────────────────────────
// dev 环境默认已激活，避免挡住 UI 开发；联调激活页可将其改为未激活。
const mockLicense: LicenseInfo = {
  activated: true,
  status: 'valid',
  message: '已授权给 演示客户，有效期至 2099-12-31。',
  machine_code: 'AAAA-AAAA-AAAA-AAAA-AAAA-AAAA',
  licensee: '演示客户',
  license_id: 'POIR-DEV-MOCK',
  expires_at: '2099-12-31',
  ok: true,
}

const mockLogs = [
  { time: '10:00:01', level: 'INFO', message: '已读取 3 条有效 URL，开始准备任务目录。', evidence_id: null },
  { time: '10:00:03', level: 'INFO', message: '模板副本准备完成，开始抓取。', evidence_id: null },
]

const mockAuthPlatforms: AuthPlatform[] = [
  {
    key: 'weibo',
    name: '新浪微博',
    status: 'unknown',
    status_text: '未检查',
    tone: 'muted',
    message: '尚未验证过该平台。',
    account: '',
    relevant: false,
  },
  {
    key: 'bilibili',
    name: '哔哩哔哩',
    status: 'auth_required',
    status_text: '需要登录',
    tone: 'warn',
    message: '尚未保存已验证登录态。',
    account: '',
    relevant: true,
  },
]

const mockLlmSettings: LlmSettingsPayload = {
  enabled: true,
  base_url: 'https://api.openai.com/v1',
  model: 'deepseek-chat',
  timeout_seconds: 30,
  max_input_chars: 6000,
  has_api_key: false,
  api_key_masked: '',
}

function updateMockAuth(key: string, patch: Partial<AuthPlatform>) {
  const platform = mockAuthPlatforms.find((item) => item.key === key)
  if (!platform) return null
  Object.assign(platform, patch)
  window.__poir_event?.({ type: 'auth', payload: { ...platform } })
  return platform
}

const mockSheets: SheetPayload[] = [
  {
    name: '图文视频',
    manual_row_allowed: false,
    columns: [
      { key: 'A', header: 'URL(必填)', field: 'url', editable: false, required: true, multiline: false, choices: [], kind: 'url' },
      { key: 'B', header: '用户账号(必填)', field: 'author_id', editable: true, required: true, multiline: false, choices: [], kind: 'text' },
      { key: 'C', header: '昵称(必填)', field: 'author_name', editable: true, required: true, multiline: false, choices: [], kind: 'text' },
      { key: 'D', header: '发布平台(必填)', field: 'platform', editable: true, required: true, multiline: false, choices: ['快手科技_快手_图文视频', '行吟科技_小红书_图文视频', '幻电科技_哔哩哔哩_图文视频'], kind: 'text' },
      { key: 'G', header: '信息内容(必填)', field: 'content', editable: true, required: true, multiline: true, choices: [], kind: 'text' },
      { key: 'H', header: '账号截图名(必填)', field: null, editable: false, required: true, multiline: false, choices: [], kind: 'screenshot' },
    ],
    rows: [
      {
        eid: 1,
        cells: { A: 'https://www.bilibili.com/video/BV1xx411c7mD', B: '10086', C: 'UP主甲', D: '幻电科技_哔哩哔哩_图文视频', G: '这是正文内容。', H: '001_shot.jpg' },
        status: 'exported',
        status_text: '成功',
        attention: false,
        missing: [],
        manual: false,
        url: 'https://www.bilibili.com/video/BV1xx411c7mD',
        final_url: '',
      },
      {
        eid: 2,
        cells: { A: 'https://www.xiaohongshu.com/explore/abc123', B: '', C: '', D: '', G: '', H: '' },
        status: 'needs_review',
        status_text: '待补录',
        attention: true,
        missing: ['用户账号', '昵称', '发布平台', '信息内容', '截图'],
        manual: false,
        url: 'https://www.xiaohongshu.com/explore/abc123',
        final_url: '',
      },
    ],
  },
  {
    name: '群聊',
    manual_row_allowed: true,
    columns: [
      { key: 'C', header: '发布平台(必填)', field: 'platform', editable: true, required: true, multiline: false, choices: ['微信-群聊', 'QQ-群聊'], kind: 'text' },
      { key: 'F', header: '信息内容(必填)', field: 'content', editable: true, required: true, multiline: true, choices: [], kind: 'text' },
      { key: 'H', header: '群聊截图文件名', field: null, editable: false, required: false, multiline: false, choices: [], kind: 'screenshot' },
    ],
    rows: [
      {
        eid: 3,
        cells: { C: '', F: '', H: '' },
        status: 'needs_review',
        status_text: '待补录',
        attention: true,
        missing: ['发布平台', '信息内容'],
        manual: true,
        url: '',
        final_url: '',
      },
    ],
  },
]

function mockCall<T>(method: string, ...args: unknown[]): Promise<T> {
  const respond = (value: unknown) => Promise.resolve(value as T)
  switch (method) {
    case 'get_bootstrap':
      return respond({
        options: {
          max_concurrency: 3,
          page_timeout_seconds: 45,
          max_retries: 1,
          screenshot_format: 'jpeg',
          headless: false,
        },
        has_checkpoint: false,
        session: {
          job_dir: 'D:/demo/output/job-demo',
          done: 1,
          total: 3,
          sheets: [
            { name: '图文视频', done: 1, total: 2 },
            { name: '群聊', done: 0, total: 1 },
          ],
        },
        license: { ...mockLicense },
      })
    case 'license_status':
      return respond({ ...mockLicense })
    case 'license_activate':
      Object.assign(mockLicense, {
        activated: true,
        status: 'valid',
        message: '已授权给 演示客户，有效期至 2099-12-31。',
        ok: true,
      })
      return respond({ ...mockLicense })
    case 'license_deactivate':
      Object.assign(mockLicense, {
        activated: false,
        status: 'not_activated',
        message: '尚未激活，请输入授权码完成激活。',
        ok: false,
      })
      return respond({ ...mockLicense })
    case 'pick_input_file':
      return respond({ path: 'D:/demo/urls.txt', url_count: 3, rejected_count: 0 })
    case 'get_sheet_payload':
      return respond(mockSheets)
    case 'apply_edit': {
      const [, field, value] = args as [number, string, string]
      for (const sheet of mockSheets) {
        const row = sheet.rows.find((r) => r.eid === (args[0] as number))
        if (!row) continue
        const column = sheet.columns.find((c) => c.field === field)
        if (column) row.cells[column.key] = value
        row.missing = row.missing.filter((m) => !column?.header.startsWith(m))
        row.attention = row.missing.length > 0
        row.status_text = row.attention ? '待补录' : '成功'
        return respond({
          ok: true,
          row: { missing: row.missing, attention: row.attention, status_text: row.status_text },
        })
      }
      return respond({ ok: false })
    }
    case 'auth_list':
      return respond(mockAuthPlatforms.map((item) => ({ ...item })))
    case 'auth_login': {
      const [key] = args as [string]
      updateMockAuth(key, {
        status: 'waiting_user',
        status_text: '等待完成登录',
        tone: 'muted',
        message: '登录页已稳定打开；请完成登录后返回并保存。',
      })
      return respond({ ok: true, message: '' })
    }
    case 'auth_confirm': {
      const [key] = args as [string]
      updateMockAuth(key, {
        status: 'valid',
        status_text: '登录态有效',
        tone: 'ok',
        message: '已验证并保存登录态。',
        account: '演示账号',
      })
      return respond({ ok: true, message: '正在检查登录结果，成功后会保存并关闭登录窗口。' })
    }
    case 'auth_cancel': {
      const [key] = args as [string]
      updateMockAuth(key, {
        status: 'auth_required',
        status_text: '需要登录',
        tone: 'warn',
        message: '已取消本次登录；原有登录态不会被覆盖。',
      })
      return respond({ ok: true, message: '已取消本次登录；原有登录态不会被覆盖。' })
    }
    case 'auth_probe': {
      const [key] = args as [string]
      const platform = mockAuthPlatforms.find((item) => item.key === key)
      if (platform?.status !== 'valid') {
        updateMockAuth(key, {
          status: 'auth_required',
          status_text: '需要登录',
          tone: 'warn',
          message: '未检测到可用登录态。',
        })
      } else {
        updateMockAuth(key, { message: '登录态验证通过。' })
      }
      return respond({ ok: true, message: '' })
    }
    case 'auth_probe_relevant':
      return respond({ ok: true, message: '' })
    case 'auth_resume_login':
      return respond({ ok: true, message: '' })
    case 'get_llm_settings':
      return respond({ ok: true, settings: { ...mockLlmSettings } })
    case 'save_llm_settings': {
      const [payload] = args as [LlmSavePayload]
      Object.assign(mockLlmSettings, payload, {
        has_api_key: Boolean(payload.api_key) || mockLlmSettings.has_api_key,
        api_key_masked: payload.api_key ? 'sk-d…1234' : mockLlmSettings.api_key_masked,
      })
      return respond({ ok: true, settings: { ...mockLlmSettings } })
    }
    case 'test_llm_connection': {
      window.setTimeout(
        () =>
          window.__poir_event?.({
            type: 'llm_test',
            payload: { ok: true, message: '连接成功，模型响应正常。', latency_ms: 620, reply: '正常' },
          }),
        400,
      )
      return respond({ ok: true, message: '正在测试连接…' })
    }
    case 'list_screenshots':
      return respond({ content: null, author: null })
    case 'list_invalid_url_candidates':
      return respond({ ok: true, rows: [] })
    case 'keep_invalid_url_candidates':
      return respond({ ok: true, kept: 0 })
    case 'start_region_capture':
      return respond({ ok: true, message: '' })
    case 'start_crawl': {
      // 模拟一次完整任务：started → progress → finished，驱动向导自动前进
      const emit = (type: string, payload: unknown) =>
        window.__poir_event?.({ type: type as never, payload })
      window.setTimeout(() => emit('started', { job_id: 'demo-0001', label: '批量抓取', total: 3, rejected_count: 0 }), 200)
      window.setTimeout(
        () =>
          emit('progress', {
            completed: 2,
            total: 3,
            ready: 1,
            needs_review: 1,
            failed: 0,
            cancelled: 0,
            current_url: 'https://www.bilibili.com/video/BV1xx411c7mD',
            stage: '抓取中',
            percent: 67,
          }),
        600,
      )
      window.setTimeout(
        () =>
          emit('finished', {
            job_id: 'demo-0001',
            label: '批量抓取',
            archive_path: null,
            final_copy_path: null,
            manual_entry_path: null,
            cancelled: false,
            ready: 1,
            needs_review: 2,
            failed: 0,
            cancelled_count: 0,
            retryable: 2,
          }),
        1200,
      )
      return respond({ ok: true, message: '' })
    }
    default:
      // eslint-disable-next-line no-console
      console.info(`[mock] ${method}`, ...args)
      return respond({ ok: true, message: '', eid: null, skipped: 0, copied: 0, name: '' })
  }
}

export const bridge = {
  getBootstrap: () => call<Bootstrap>('get_bootstrap'),
  licenseStatus: () => call<LicenseInfo>('license_status'),
  licenseActivate: (code: string) => call<LicenseInfo>('license_activate', code),
  licenseDeactivate: () => call<LicenseInfo>('license_deactivate'),
  // ── 浏览器上传（B/S 替代原生文件对话框）──
  uploadInputFile: async (file: File): Promise<InputFileInfo & { ok?: boolean; message?: string; error?: string }> => {
    if ((await detectTransport()) === 'mock')
      return { path: `D:/demo/${file.name}`, url_count: 3, rejected_count: 0 }
    return uploadFiles('/api/upload/input', 'file', [file])
  },
  uploadZipFile: async (file: File): Promise<{ ok: boolean; message: string }> => {
    if ((await detectTransport()) === 'mock') return { ok: true, message: '' }
    return uploadFiles('/api/upload/zip', 'file', [file])
  },
  uploadLetterFiles: async (
    files: File[],
  ): Promise<{ ok: boolean; names: string[]; message: string }> => {
    if ((await detectTransport()) === 'mock')
      return { ok: true, names: files.map((f) => f.name), message: '' }
    return uploadFiles('/api/upload/letter', 'files', files)
  },
  uploadScreenshot: async (
    eid: number,
    mode: 'primary' | 'author' | 'attachment',
    file: File,
  ): Promise<{ ok: boolean; name: string }> => {
    if ((await detectTransport()) === 'mock') return { ok: true, name: file.name }
    return uploadFiles('/api/upload/screenshot', 'file', [file], {
      evidence_id: String(eid),
      mode,
    })
  },
  uploadAuthState: async (
    key: string,
    file: File,
  ): Promise<{ ok: boolean; message: string }> => {
    if ((await detectTransport()) === 'mock') return { ok: true, message: '' }
    return uploadFiles('/api/upload/auth-state', 'file', [file], { platform: key })
  },
  // ── 浏览器下载（B/S 替代「打开输出目录 / SAVE 对话框」）──
  downloadJobZip: () => {
    window.location.href = '/api/download/job-zip'
  },
  downloadManualEntries: () => {
    window.location.href = '/api/download/manual-entries'
  },
  removeLetterFile: (name: string) =>
    call<{ ok: boolean; names: string[] }>('remove_letter_file', name),
  clearLetterFile: () => call<{ ok: boolean }>('clear_letter_file'),
  letterState: () => call<{ names: string[] }>('letter_state'),
  listManualEntries: () =>
    call<{
      ok: boolean
      rows: ManualEntryRow[]
      path: string
      message: string
      completed_count?: number
    }>('list_manual_entries'),
  setOptions: (o: TaskOptions) => call<{ ok: boolean }>('set_options', o),
  startCrawl: (p: string, dedupe = false) =>
    call<{ ok: boolean; message: string }>('start_crawl', p, dedupe),
  cancelJob: () => call<{ ok: boolean }>('cancel_job'),
  retryFailed: () => call<{ ok: boolean; message: string }>('retry_failed'),
  resumeCheckpoint: (reexportOnly: boolean, inputPath: string, dedupe = false) =>
    call<{ ok: boolean; message: string }>('resume_checkpoint', reexportOnly, inputPath, dedupe),
  getSheetPayload: () => call<SheetPayload[]>('get_sheet_payload'),
  applyEdit: (eid: number, field: string, value: string) =>
    call<{ ok: boolean; message?: string }>('apply_edit', eid, field, value),
  addManualRow: (sheet: string) => call<{ eid: number | null }>('add_manual_row', sheet),
  removeRecord: (eid: number) => call<{ ok: boolean }>('remove_record', eid),
  listScreenshots: (eid: number) => call<ScreenshotPair>('list_screenshots', eid),
  startRegionCapture: (eid: number, target: 'content' | 'author') =>
    call<{ ok: boolean; code?: string; message: string }>('start_region_capture', eid, target),
  listUrlRecheck: () =>
    call<{ ok: boolean; rows: UrlRecheckRow[]; running: boolean }>('list_url_recheck'),
  startUrlRecheck: () => call<{ ok: boolean; message: string }>('start_url_recheck'),
  cancelUrlRecheck: () => call<{ ok: boolean }>('cancel_url_recheck'),
  removeRecords: (eids: number[]) =>
    call<{ ok: boolean; removed: number }>('remove_records', eids),
  listInvalidUrlCandidates: () =>
    call<{ ok: boolean; rows: InvalidUrlCandidateRow[] }>('list_invalid_url_candidates'),
  keepInvalidUrlCandidates: (eids: number[]) =>
    call<{ ok: boolean; kept: number }>('keep_invalid_url_candidates', eids),
  exportZip: () => call<{ ok: boolean; message: string }>('export_zip'),
  authList: () => call<AuthPlatform[]>('auth_list'),
  authProbeAll: () => call<{ ok: boolean }>('auth_probe_all'),
  authProbeRelevant: () => call<{ ok: boolean; message: string }>('auth_probe_relevant'),
  authLoginAll: () => call<{ ok: boolean; message: string }>('auth_login_all'),
  authProbe: (key: string) => call<{ ok: boolean; message: string }>('auth_probe', key),
  authLogin: (key: string) => call<{ ok: boolean; message: string }>('auth_login', key),
  authConfirm: (key: string) => call<{ ok: boolean; message: string }>('auth_confirm', key),
  authCancel: (key: string) => call<{ ok: boolean; message: string }>('auth_cancel', key),
  authResumeLogin: (key: string, action: 'skip' | 'retry') =>
    call<{ ok: boolean; message: string }>('auth_resume_login', key, action),
  authLogout: (key: string) => call<{ ok: boolean }>('auth_logout', key),
  getLlmSettings: () => call<{ ok: boolean; settings: LlmSettingsPayload }>('get_llm_settings'),
  saveLlmSettings: (payload: LlmSavePayload) =>
    call<{ ok: boolean; message?: string; settings?: LlmSettingsPayload }>(
      'save_llm_settings',
      payload,
    ),
  testLlmConnection: () => call<{ ok: boolean; message: string }>('test_llm_connection'),
}

export { mockLogs }

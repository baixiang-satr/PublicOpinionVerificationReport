// pywebview js_api 封装。浏览器 dev 环境（无 pywebview）时使用本地 mock，
// 方便 `npm run dev` 直接开发 UI。
import type {
  AuthPlatform,
  Bootstrap,
  BridgeEvent,
  InputFileInfo,
  LicenseInfo,
  ScreenshotPair,
  SheetPayload,
  TaskOptions,
} from '@/types'

interface PyWebviewApi {
  get_bootstrap(): Promise<Bootstrap>
  license_status(): Promise<LicenseInfo>
  license_activate(code: string): Promise<LicenseInfo>
  license_deactivate(): Promise<LicenseInfo>
  pick_input_file(): Promise<InputFileInfo | null>
  pick_zip_file(): Promise<{ ok: boolean; message: string }>
  set_options(options: TaskOptions): Promise<{ ok: boolean }>
  start_crawl(input_path: string): Promise<{ ok: boolean; message: string }>
  cancel_job(): Promise<{ ok: boolean }>
  retry_failed(): Promise<{ ok: boolean; message: string }>
  resume_checkpoint(reexport_only: boolean, input_path: string): Promise<{ ok: boolean; message: string }>
  get_sheet_payload(): Promise<SheetPayload[]>
  apply_edit(eid: number, field: string, value: string): Promise<{ ok: boolean }>
  add_manual_row(sheet_name: string): Promise<{ eid: number | null }>
  remove_manual_row(eid: number): Promise<{ ok: boolean }>
  pick_screenshot(eid: number, mode: 'primary' | 'author' | 'attachment'): Promise<{ ok: boolean; name: string }>
  list_screenshots(eid: number): Promise<ScreenshotPair>
  start_region_capture(eid: number, target: 'content' | 'author'): Promise<{ ok: boolean; code?: string; message: string }>
  open_url(url: string): Promise<{ ok: boolean }>
  open_output_dir(): Promise<{ ok: boolean }>
  export_zip(): Promise<{ ok: boolean; message: string }>
  auth_list(): Promise<AuthPlatform[]>
  auth_probe_all(): Promise<{ ok: boolean }>
  auth_probe_relevant(): Promise<{ ok: boolean; message: string }>
  auth_login_all(): Promise<{ ok: boolean; message: string }>
  auth_probe(key: string): Promise<{ ok: boolean; message: string }>
  auth_login(key: string): Promise<{ ok: boolean; message: string }>
  auth_confirm(key: string): Promise<{ ok: boolean; message: string }>
  auth_cancel(key: string): Promise<{ ok: boolean; message: string }>
  auth_resume_login(key: string, action: string): Promise<{ ok: boolean; message: string }>
  auth_logout(key: string): Promise<{ ok: boolean }>
}

declare global {
  interface Window {
    pywebview?: { api: PyWebviewApi }
    __poir_event?: (event: BridgeEvent) => void
  }
}

export function hasBridge(): boolean {
  return typeof window !== 'undefined' && !!window.pywebview?.api
}

async function call<T>(method: keyof PyWebviewApi, ...args: unknown[]): Promise<T> {
  const result = hasBridge()
    ? await (window.pywebview!.api as unknown as Record<string, (...a: unknown[]) => Promise<T>>)[method](
        ...args,
      )
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
    case 'list_screenshots':
      return respond({ content: null, author: null })
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
  pickInputFile: () => call<InputFileInfo | null>('pick_input_file'),
  pickZipFile: () => call<{ ok: boolean; message: string }>('pick_zip_file'),
  setOptions: (o: TaskOptions) => call<{ ok: boolean }>('set_options', o),
  startCrawl: (p: string) => call<{ ok: boolean; message: string }>('start_crawl', p),
  cancelJob: () => call<{ ok: boolean }>('cancel_job'),
  retryFailed: () => call<{ ok: boolean; message: string }>('retry_failed'),
  resumeCheckpoint: (reexportOnly: boolean, inputPath: string) =>
    call<{ ok: boolean; message: string }>('resume_checkpoint', reexportOnly, inputPath),
  getSheetPayload: () => call<SheetPayload[]>('get_sheet_payload'),
  applyEdit: (eid: number, field: string, value: string) =>
    call<{ ok: boolean }>('apply_edit', eid, field, value),
  addManualRow: (sheet: string) => call<{ eid: number | null }>('add_manual_row', sheet),
  removeManualRow: (eid: number) => call<{ ok: boolean }>('remove_manual_row', eid),
  pickScreenshot: (eid: number, mode: 'primary' | 'author' | 'attachment') =>
    call<{ ok: boolean; name: string }>('pick_screenshot', eid, mode),
  listScreenshots: (eid: number) => call<ScreenshotPair>('list_screenshots', eid),
  startRegionCapture: (eid: number, target: 'content' | 'author') =>
    call<{ ok: boolean; code?: string; message: string }>('start_region_capture', eid, target),
  openUrl: (url: string) => call<{ ok: boolean }>('open_url', url),
  openOutputDir: () => call<{ ok: boolean }>('open_output_dir'),
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
}

export { mockLogs }

import { defineStore } from 'pinia'

import { bridge } from '@/api/bridge'
import type {
  AuthPlatform,
  AuthReloginPayload,
  BridgeEvent,
  CaptureEventPayload,
  JobFinishedPayload,
  JobStartedPayload,
  LicenseInfo,
  LogPayload,
  ProgressPayload,
  SessionOverview,
  TaskOptions,
} from '@/types'

export const STEPS = [
  { title: '欢迎', desc: '选择开始方式' },
  { title: '输入与参数', desc: 'URL 文件 · 登录态' },
  { title: '抓取执行', desc: '进度与取消' },
  { title: '抓取结果', desc: 'template 表格预览' },
  { title: '采集与补录', desc: '可编辑网格' },
  { title: '预览导出', desc: '生成 template.zip' },
] as const

const EMPTY_PROGRESS: ProgressPayload = {
  completed: 0,
  total: 0,
  ready: 0,
  needs_review: 0,
  failed: 0,
  cancelled: 0,
  current_url: '',
  stage: '准备中',
  percent: 0,
}

interface JobState {
  step: number
  furthest: number
  options: TaskOptions | null
  license: LicenseInfo | null
  inputPath: string
  urlCount: number
  running: boolean
  started: JobStartedPayload | null
  progress: ProgressPayload
  logs: LogPayload[]
  session: SessionOverview | null
  lastArchive: string
  retryable: number
  statusText: string
  authDialogOpen: boolean
  authPlatforms: AuthPlatform[]
  relogin: AuthReloginPayload | null
  sheetDialogOpen: boolean
  sheetDialogMode: 'preview' | 'edit'
  lastCapture: CaptureEventPayload | null
}

export const useJobStore = defineStore('job', {
  state: (): JobState => ({
    step: 0,
    furthest: 0,
    options: null,
    license: null,
    inputPath: '',
    urlCount: 0,
    running: false,
    started: null,
    progress: { ...EMPTY_PROGRESS },
    logs: [],
    session: null,
    lastArchive: '',
    retryable: 0,
    statusText: '系统就绪',
    authDialogOpen: false,
    authPlatforms: [],
    relogin: null,
    sheetDialogOpen: false,
    sheetDialogMode: 'preview',
    lastCapture: null,
  }),
  getters: {
    canGoNext(state): boolean {
      if (state.running) return false
      if (state.step === 1) return state.inputPath.length > 0
      if (state.step === 3 || state.step === 4) return state.session !== null
      return true
    },
    nextLabel(state): string {
      switch (state.step) {
        case 0:
          return '开始 →'
        case 1:
          return '开始抓取 →'
        case 3:
          return '去补录 →'
        case 4:
          return '预览导出 →'
        case 5:
          return '回到首页 ↺'
        default:
          return '下一步 →'
      }
    },
  },
  actions: {
    async bootstrap() {
      const data = await bridge.getBootstrap()
      this.options = data.options
      this.session = data.session
      this.license = data.license
      window.__poir_event = (event: BridgeEvent) => this.handleEvent(event)
      window.addEventListener('poir-license-required', () => {
        void this.refreshLicense()
      })
    },
    async activateLicense(code: string): Promise<LicenseInfo> {
      this.license = await bridge.licenseActivate(code)
      return this.license
    },
    async deactivateLicense(): Promise<void> {
      this.license = await bridge.licenseDeactivate()
    },
    async refreshLicense(): Promise<void> {
      // 被守卫拦截（LICENSE_REQUIRED）后刷新授权状态；未激活时 App 自动切激活页
      this.license = await bridge.licenseStatus()
    },
    handleEvent(event: BridgeEvent) {
      const payload = event.payload as Record<string, unknown>
      switch (event.type) {
        case 'started':
          this.started = payload as unknown as JobStartedPayload
          this.progress = { ...EMPTY_PROGRESS, total: this.started.total }
          this.statusText = `任务 ${this.started.job_id} 运行中`
          break
        case 'progress':
          this.progress = payload as unknown as ProgressPayload
          break
        case 'log':
          this.logs.push(payload as unknown as LogPayload)
          if (this.logs.length > 500) this.logs.splice(0, this.logs.length - 500)
          break
        case 'session':
          void this.refreshSession()
          break
        case 'finished':
          this.onFinished(payload as unknown as JobFinishedPayload)
          break
        case 'failed':
          this.running = false
          this.relogin = null
          this.statusText = `任务失败：${payload.message ?? ''}`
          break
        case 'auth':
          this.onAuthEvent(payload as unknown as AuthPlatform)
          break
        case 'auth_relogin':
          this.onAuthRelogin(payload as unknown as AuthReloginPayload)
          break
        case 'capture':
          this.onCaptureEvent(payload as unknown as CaptureEventPayload)
          break
      }
    },
    onFinished(result: JobFinishedPayload) {
      this.running = false
      this.relogin = null
      this.retryable = result.retryable
      this.lastArchive = result.archive_path ?? this.lastArchive
      this.statusText = result.cancelled ? '任务已取消' : '任务完成'
      void this.refreshSession()
      if (!result.cancelled && this.step === 2) {
        this.goTo(3)
      }
    },
    onAuthEvent(platform: AuthPlatform) {
      const index = this.authPlatforms.findIndex((p) => p.key === platform.key)
      if (index >= 0) {
        platform.relevant = this.authPlatforms[index].relevant
        this.authPlatforms[index] = platform
      } else {
        platform.relevant = false
        this.authPlatforms.push(platform)
      }
    },
    onAuthRelogin(payload: AuthReloginPayload) {
      // 抓取中发现登录态失效：done 表示本次重登流程结束（成功/跳过/取消）
      this.statusText = payload.message
      this.relogin = payload.phase === 'done' ? null : payload
    },
    onCaptureEvent(capture: CaptureEventPayload) {
      this.lastCapture = capture
      if (capture.status === 'saved') {
        this.statusText = `已保存截图 ${capture.name}`
      } else if (capture.status === 'error') {
        this.statusText = `截图失败：${capture.message}`
      }
    },
    async refreshSession() {
      const data = await bridge.getBootstrap()
      this.session = data.session
      this.options = this.options ?? data.options
    },
    goTo(step: number) {
      this.step = Math.max(0, Math.min(STEPS.length - 1, step))
      this.furthest = Math.max(this.furthest, this.step)
    },
    next() {
      if (this.step === 5) {
        this.resetForHome()
        this.goTo(0)
        return
      }
      if (this.step === 1 && this.options) {
        void bridge.setOptions(this.options)
      }
      this.goTo(this.step + 1)
    },
    back() {
      this.goTo(this.step - 1)
    },
    startNewTask() {
      this.resetForHome()
      this.goTo(1)
    },
    resetForHome() {
      this.inputPath = ''
      this.urlCount = 0
      this.started = null
      this.progress = { ...EMPTY_PROGRESS }
      this.logs = []
      this.retryable = 0
    },
    resetRunState() {
      this.started = null
      this.progress = { ...EMPTY_PROGRESS }
      this.logs = []
    },
    async refreshAuth() {
      this.authPlatforms = await bridge.authList()
    },
    openSheetDialog(mode: 'preview' | 'edit') {
      this.sheetDialogMode = mode
      this.sheetDialogOpen = true
    },
  },
})

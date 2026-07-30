import { defineStore } from 'pinia'

import { bridge } from '@/api/bridge'
import type {
  AuthPlatform,
  BridgeEvent,
  JobFinishedPayload,
  JobStartedPayload,
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
  sheetDialogOpen: boolean
  sheetDialogMode: 'preview' | 'edit'
}

export const useJobStore = defineStore('job', {
  state: (): JobState => ({
    step: 0,
    furthest: 0,
    options: null,
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
    sheetDialogOpen: false,
    sheetDialogMode: 'preview',
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
      window.__poir_event = (event: BridgeEvent) => this.handleEvent(event)
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
          this.statusText = `任务失败：${payload.message ?? ''}`
          break
        case 'auth':
          this.onAuthEvent(payload as unknown as AuthPlatform)
          break
      }
    },
    onFinished(result: JobFinishedPayload) {
      this.running = false
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
      if (index >= 0) this.authPlatforms[index] = platform
      else this.authPlatforms.push(platform)
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

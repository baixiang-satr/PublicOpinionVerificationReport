<script setup lang="ts">
// 第 2 步：抓取执行 — 进度、取消、高级操作（重试/断点/仅重新导出）、运行日志。
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'

const store = useJobStore()
const showAdvanced = ref(false)
const showLog = ref(false)
const logRef = ref<HTMLElement>()

const progressStatus = computed(() => {
  if (store.progress.failed > 0 && !store.running) return 'exception'
  if (!store.running && store.progress.completed > 0 && store.progress.percent >= 100)
    return 'success'
  return undefined
})

async function start() {
  if (!store.inputPath) {
    ElMessage.warning('请回到上一步选择 URL 文件。')
    return
  }
  store.resetRunState()
  store.running = true
  store.statusText = '正在启动任务…'
  if (store.options) await bridge.setOptions(store.options)
  const res = await bridge.startCrawl(store.inputPath)
  if (!res.ok) {
    store.running = false
    store.statusText = res.message || '任务启动失败'
    ElMessage.error(res.message || '任务启动失败')
  }
}

async function cancel() {
  await ElMessageBox.confirm('确定取消当前任务吗？取消不会生成不完整的压缩包。', '取消任务', {
    type: 'warning',
  })
  await bridge.cancelJob()
  store.statusText = '正在取消…'
}

async function retry() {
  store.resetRunState()
  store.running = true
  const res = await bridge.retryFailed()
  if (!res.ok) {
    store.running = false
    ElMessage.warning(res.message || '没有可重试的失败项。')
  }
}

async function resume(reexportOnly: boolean) {
  store.resetRunState()
  store.running = true
  const res = await bridge.resumeCheckpoint(reexportOnly, store.inputPath)
  if (!res.ok) {
    store.running = false
    ElMessage.warning(res.message || '没有可用的断点。')
  }
}

watch(
  () => store.logs.length,
  async () => {
    await nextTick()
    logRef.value?.scrollTo({ top: logRef.value.scrollHeight })
  },
)
</script>

<template>
  <section>
    <h1 class="page-title">第 2 步 · 抓取执行</h1>
    <p class="page-subtitle muted">
      抓取过程全自动进行，你可以随时取消；取消不会生成不完整的压缩包。
    </p>
    <div class="notice-banner">
      抓取完成后会自动进入「抓取结果」。失败或受限的 URL 不用慌，下一步可以人工补录。
    </div>

    <div class="card">
      <div class="progress-head">
        <el-progress
          :percentage="store.progress.percent"
          :status="progressStatus"
          :stroke-width="14"
          class="progress-bar"
        />
        <span class="muted">{{ store.progress.stage }}</span>
      </div>
      <div class="progress-stats">
        <span>共 {{ store.progress.total }} 条</span>
        <span>已完成 {{ store.progress.completed }}</span>
        <span class="ok">成功 {{ store.progress.ready }}</span>
        <span class="warn">待补录 {{ store.progress.needs_review }}</span>
        <span class="err">失败 {{ store.progress.failed }}</span>
      </div>
      <div v-if="store.progress.current_url" class="muted current-url">
        正在处理：{{ store.progress.current_url }}
      </div>
      <div class="action-row">
        <el-button type="primary" :disabled="store.running" @click="start">
          开始抓取
        </el-button>
        <el-button type="danger" plain :disabled="!store.running" @click="cancel">
          取消任务
        </el-button>
        <el-button text @click="showAdvanced = !showAdvanced">
          高级操作（重试 / 断点续传）{{ showAdvanced ? '▴' : '▾' }}
        </el-button>
        <el-button text @click="showLog = !showLog">
          运行日志 {{ showLog ? '▴' : '▾' }}
        </el-button>
      </div>
    </div>

    <div v-show="showAdvanced" class="card">
      <h2 class="card-title">高级操作</h2>
      <div class="action-row">
        <el-button :disabled="store.running || store.retryable === 0" @click="retry">
          重试失败项
        </el-button>
        <el-button :disabled="store.running" @click="resume(false)">从断点继续</el-button>
        <el-button :disabled="store.running" @click="resume(true)">仅重新导出</el-button>
      </div>
      <p class="muted action-hint">
        重试失败项：保留已成功记录，只重新抓取失败和待补录的 URL；
        仅重新导出：不启动浏览器，直接用断点记录重新生成 template.zip。
      </p>
    </div>

    <div v-show="showLog" class="card">
      <h2 class="card-title">运行日志</h2>
      <div ref="logRef" class="log-panel">
        <div v-for="(log, i) in store.logs" :key="i" class="log-line">
          <span class="muted">{{ log.time }}</span>
          <span class="log-level" :class="log.level.toLowerCase()">{{ log.level }}</span>
          <span>{{ log.message }}</span>
        </div>
        <div v-if="store.logs.length === 0" class="muted">暂无日志。</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.progress-head {
  display: flex;
  align-items: center;
  gap: 14px;
}

.progress-bar {
  flex: 1;
}

.progress-stats {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 13px;
}

.ok {
  color: #67c23a;
}
.warn {
  color: #e6a23c;
}
.err {
  color: #f56c6c;
}

.current-url {
  margin-top: 6px;
  word-break: break-all;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 14px;
}

.action-hint {
  margin: 10px 0 0;
  line-height: 1.6;
}

.log-panel {
  max-height: 260px;
  overflow-y: auto;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  background: #fafafa;
  border: 1px solid var(--poir-border);
  border-radius: 6px;
  padding: 8px 10px;
}

.log-line {
  display: flex;
  gap: 8px;
  line-height: 1.7;
}

.log-level.info {
  color: var(--el-color-primary);
}
.log-level.warning {
  color: #e6a23c;
}
.log-level.error {
  color: #f56c6c;
}
</style>

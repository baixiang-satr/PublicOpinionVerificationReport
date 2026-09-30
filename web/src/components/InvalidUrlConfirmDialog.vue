<script setup lang="ts">
// URL 失效确认弹窗：抓取完成后自动弹出，逐条列出失效候选的 URL 与判定依据
// 引文；默认主按钮为「保留」（含 X 关闭，均落盘 keep 决策）；删除需显式勾选
// 并二次确认，走 remove_records（资产清理 + 候选 prune + deleted 留痕）。
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'
import type { InvalidUrlCandidateRow } from '@/types'

const store = useJobStore()

const selected = ref<InvalidUrlCandidateRow[]>([])

async function keepAll() {
  // 「保留」与关闭同义：为全部候选落盘 keep 决策，保证用户选择留痕。
  const eids = store.invalidCandidates.map((row) => row.eid)
  if (eids.length) {
    try {
      await bridge.keepInvalidUrlCandidates(eids)
    } catch {
      // 留痕失败不阻断关闭；候选仍为待决策，下次完成后会再次弹出
    }
  }
  store.invalidCandidates = []
}

async function keepAndClose() {
  await keepAll()
  store.invalidDialogOpen = false
  ElMessage.info('已保留全部记录。')
}

function handleRequestClose(done: () => void) {
  void keepAll()
  done()
}

async function removeSelected() {
  if (!selected.value.length) return
  await ElMessageBox.confirm(
    `即将删除选中的 ${selected.value.length} 条失效记录，它们的截图与人工填写内容都会一并删除。删除后可重新导出交付包。`,
    '删除失效记录',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  const eids = selected.value.map((row) => row.eid)
  const res = await bridge.removeRecords(eids)
  ElMessage.success(`已删除 ${res.removed} 条记录。`)
  store.invalidCandidates = store.invalidCandidates.filter((row) => !eids.includes(row.eid))
  if (!store.invalidCandidates.length) store.invalidDialogOpen = false
}

function codeLabel(code: string): string {
  switch (code) {
    case 'CONTENT_NOT_FOUND':
      return 'HTTP 404'
    case 'CONTENT_UNAVAILABLE':
      return '规则确证'
    case 'CONTENT_REDIRECTED_TO_HOME':
      return '重定向首页'
    case 'CONTENT_DELETED_LLM':
      return '大模型判定'
    default:
      return code || '判定'
  }
}

function formatTime(iso: string): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : ''
}
</script>

<template>
  <el-dialog
    v-model="store.invalidDialogOpen"
    title="疑似失效链接确认"
    width="920px"
    :close-on-click-modal="false"
    :before-close="handleRequestClose"
  >
    <p class="muted tip">
      抓取过程中发现 {{ store.invalidCandidates.length }} 条链接疑似已失效（判定依据为页面原文引文）。
      点击 URL 可在浏览器新标签中打开原页面核实。默认全部保留；如需删除，请显式勾选后再点「删除选中记录」。
    </p>
    <el-table
      :data="store.invalidCandidates"
      height="420"
      size="small"
      @selection-change="(list: InvalidUrlCandidateRow[]) => (selected = list)"
    >
      <el-table-column type="selection" width="42" />
      <el-table-column prop="eid" label="编号" width="70">
        <template #default="{ row }">{{ String(row.eid).padStart(3, '0') }}</template>
      </el-table-column>
      <el-table-column prop="url" label="URL（点击打开原页面核实）" min-width="300" show-overflow-tooltip>
        <template #default="{ row }">
          <a
            v-if="row.url"
            class="url-cell"
            :href="row.url"
            target="_blank"
            rel="noopener noreferrer"
            @click.stop
          >{{ row.url }}</a>
          <span v-else class="muted">（无 URL）</span>
        </template>
      </el-table-column>
      <el-table-column label="判定方式" width="100">
        <template #default="{ row }">
          <el-tag type="danger" effect="plain" size="small">{{ codeLabel(row.code) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="判定依据（原文引文）" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.message || '—' }}</template>
      </el-table-column>
      <el-table-column label="判定时间" width="150">
        <template #default="{ row }">{{ formatTime(row.checked_at) }}</template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button
        type="danger"
        plain
        :disabled="!selected.length"
        @click="removeSelected"
      >
        删除选中记录（{{ selected.length }}）
      </el-button>
      <el-button type="primary" @click="keepAndClose">保留</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tip {
  margin: 0 0 10px;
  font-size: 12px;
}

.url-cell {
  font-size: 12px;
  word-break: break-all;
  color: var(--el-color-primary);
  text-decoration: none;
}

.url-cell:hover {
  text-decoration: underline;
}
</style>

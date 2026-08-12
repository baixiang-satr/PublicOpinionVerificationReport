<script setup lang="ts">
// U04：URL 有效性复验对话框——逐条复验、三态标记、批量删除失效行。
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'
import type { UrlRecheckRow } from '@/types'

const store = useJobStore()

const rows = ref<UrlRecheckRow[]>([])
const selected = ref<UrlRecheckRow[]>([])
const loading = ref(false)

const recheckableCount = computed(() => rows.value.filter((r) => r.url).length)
const checkedCount = computed(() => rows.value.filter((r) => r.recheck).length)
const invalidRows = computed(() => rows.value.filter((r) => r.recheck?.status === 'invalid'))
const selectedDeletable = computed(() => selected.value.length)

async function reload() {
  loading.value = true
  try {
    const res = await bridge.listUrlRecheck()
    rows.value = res.ok ? res.rows : []
    store.recheckRunning = res.running
  } finally {
    loading.value = false
  }
}

watch(
  () => store.recheckDialogOpen,
  (open) => {
    if (open) void reload()
  },
)
// 复验进行中每条结果/完成事件都会推进 recheckVersion，就地刷新列表
watch(
  () => store.recheckVersion,
  () => {
    if (store.recheckDialogOpen) void reload()
  },
)

async function startRecheck() {
  const res = await bridge.startUrlRecheck()
  if (!res.ok) {
    ElMessage.warning(res.message || '无法开始复验。')
    return
  }
  store.recheckRunning = true
  ElMessage.info('开始逐条复验 URL 有效性（只探测，不重新截图）。')
}

async function cancelRecheck() {
  await bridge.cancelUrlRecheck()
  ElMessage.info('正在取消复验…')
}

async function removeRows(targets: UrlRecheckRow[], label: string) {
  if (!targets.length) return
  await ElMessageBox.confirm(
    `确定删除${label}吗？它们的截图与人工填写内容都会一并删除。删除后可重新导出交付包。`,
    '删除记录',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  const res = await bridge.removeRecords(targets.map((r) => r.eid))
  ElMessage.success(`已删除 ${res.removed} 条记录。`)
  await reload()
}

function removeSelected() {
  void removeRows(selected.value, `选中的 ${selected.value.length} 条记录`)
}

function removeAllInvalid() {
  void removeRows(invalidRows.value, `全部 ${invalidRows.value.length} 条「已失效」记录`)
}

function recheckTag(row: UrlRecheckRow): { text: string; type: 'success' | 'danger' | 'warning' | 'info' } {
  if (!row.url) return { text: '无 URL', type: 'info' }
  const entry = row.recheck
  if (!entry) return { text: '未复验', type: 'info' }
  if (entry.status === 'valid') return { text: '有效', type: 'success' }
  if (entry.status === 'invalid') return { text: '已失效', type: 'danger' }
  return { text: '存疑', type: 'warning' }
}

function formatTime(iso: string): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : ''
}
</script>

<template>
  <el-dialog
    v-model="store.recheckDialogOpen"
    title="URL 有效性复验"
    width="920px"
    :close-on-click-modal="false"
  >
    <div class="toolbar">
      <template v-if="!store.recheckRunning">
        <el-button type="primary" :disabled="!recheckableCount" @click="startRecheck">
          开始复验（{{ recheckableCount }} 条）
        </el-button>
      </template>
      <el-button v-else type="warning" @click="cancelRecheck">取消复验</el-button>
      <el-button
        type="danger"
        plain
        :disabled="store.recheckRunning || !selectedDeletable"
        @click="removeSelected"
      >
        删除选中（{{ selectedDeletable }}）
      </el-button>
      <el-button
        type="danger"
        :disabled="store.recheckRunning || !invalidRows.length"
        @click="removeAllInvalid"
      >
        一键删除全部已失效（{{ invalidRows.length }}）
      </el-button>
      <span class="muted progress-text">
        已复验 {{ checkedCount }}/{{ recheckableCount }} 条
      </span>
    </div>
    <p class="muted tip">
      「存疑」多为登录墙/验证码/风控/超时，不会被一键删除，请人工核对后勾选删除。
    </p>
    <el-table
      v-loading="loading"
      :data="rows"
      height="420"
      size="small"
      :row-class-name="(p: { row: UrlRecheckRow }) => (p.row.recheck?.status === 'invalid' ? 'invalid-row' : '')"
      @selection-change="(list: UrlRecheckRow[]) => (selected = list)"
    >
      <el-table-column type="selection" width="42" />
      <el-table-column prop="eid" label="编号" width="70">
        <template #default="{ row }">{{ String(row.eid).padStart(3, '0') }}</template>
      </el-table-column>
      <el-table-column prop="url" label="URL" min-width="300" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.url" class="url-cell">{{ row.url }}</span>
          <span v-else class="muted">（手工行，无 URL）</span>
        </template>
      </el-table-column>
      <el-table-column label="复验状态" width="90">
        <template #default="{ row }">
          <el-tag :type="recheckTag(row).type" effect="plain" size="small">
            {{ recheckTag(row).text }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="说明" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.recheck?.message || '' }}</template>
      </el-table-column>
      <el-table-column label="复验时间" width="150">
        <template #default="{ row }">{{ formatTime(row.recheck?.checked_at ?? '') }}</template>
      </el-table-column>
    </el-table>
  </el-dialog>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.progress-text {
  margin-left: auto;
  font-size: 13px;
}

.tip {
  margin: 8px 0 10px;
  font-size: 12px;
}

.url-cell {
  font-size: 12px;
  word-break: break-all;
}

/* Element Plus 单元格背景由 --el-table-tr-bg-color 变量绘制，
   直接给 tr 设 background 会被单元格盖住（失效行高亮不可见的根因）。 */
:deep(.el-table .invalid-row) {
  --el-table-tr-bg-color: #fef0f0;
}
</style>

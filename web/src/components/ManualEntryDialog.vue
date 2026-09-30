<script setup lang="ts">
// 未收录 / 待补录清单弹窗：pending_manual_entry.csv 的表格视图 + 导出 CSV。
// 覆盖两类：抓取后未达交付标准的记录 + 输入阶段被拒的无效链接（input_rejected）。
import { Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'
import type { ManualEntryRow } from '@/types'

const store = useJobStore()

const rows = ref<ManualEntryRow[]>([])
const loading = ref(false)
const exporting = ref(false)
const loadMessage = ref('')
const completedCount = ref(0)

const COLUMNS: { key: string; label: string; width?: number }[] = [
  { key: '证据编号', label: '编号', width: 72 },
  { key: '平台', label: '平台', width: 92 },
  { key: '工作表', label: '工作表', width: 104 },
  { key: '原始URL', label: '链接' },
  { key: '状态', label: '状态', width: 118 },
  { key: '错误说明', label: '未收录原因' },
  { key: '建议处理', label: '建议处理' },
]

const rejectedCount = computed(
  () => rows.value.filter((row) => row['状态'] === 'input_rejected').length,
)

watch(
  () => store.manualEntryDialogOpen,
  async (open) => {
    if (!open) return
    loading.value = true
    rows.value = []
    loadMessage.value = ''
    completedCount.value = 0
    try {
      const res = await bridge.listManualEntries()
      rows.value = res.ok ? res.rows || [] : []
      completedCount.value = res.ok ? res.completed_count || 0 : 0
      loadMessage.value = res.ok ? '' : res.message || '清单读取失败。'
    } catch (error) {
      loadMessage.value = `清单读取失败：${String(error)}`
    } finally {
      loading.value = false
    }
  },
)

async function doExport() {
  exporting.value = true
  try {
    const res = await bridge.listManualEntries()
    if (!res.ok) {
      ElMessage.warning(res.message || '当前任务还没有生成清单。')
      return
    }
    bridge.downloadManualEntries()
    ElMessage.success('清单已开始下载，请在浏览器下载记录中查看。')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="store.manualEntryDialogOpen" title="未收录 / 待补录清单" width="86%">
    <p class="muted desc">
      以下链接不会出现在 template.zip 中：抓取后仍需人工补录的记录，以及输入阶段被拒的无效链接。
      <template v-if="rejectedCount">本任务有 {{ rejectedCount }} 条输入被拒链接。</template>
      <template v-if="completedCount">另有 {{ completedCount }} 条已补录完成，已从清单隐藏。</template>
      表格可直接导出为 CSV 用 Excel 打开。
    </p>
    <el-alert v-if="loadMessage" type="info" :title="loadMessage" :closable="false" />
    <el-table v-else v-loading="loading" :data="rows" max-height="480" stripe size="small">
      <el-table-column
        v-for="col in COLUMNS"
        :key="col.key"
        :prop="col.key"
        :label="col.label"
        :width="col.width"
        show-overflow-tooltip
      />
      <template #empty>没有未收录的链接，全部记录均已收录进交付包。</template>
    </el-table>
    <template #footer>
      <el-button @click="store.manualEntryDialogOpen = false">关闭</el-button>
      <el-button
        type="primary"
        :icon="Download"
        :loading="exporting"
        :disabled="!!loadMessage"
        @click="doExport"
      >
        导出 CSV…
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.desc {
  margin: 0 0 10px;
}
</style>

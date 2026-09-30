<script setup lang="ts">
// 预览与导出 — 检查清单 + 预览表格 + 一键导出 template.zip。
import { Grid, Download } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'

const store = useJobStore()
const exporting = ref(false)

/** 交付目录：最终包所在文件夹（template.zip 与 template_final.zip 同目录）。 */
const finalDir = computed(
  () => store.lastFinalArchive?.replace(/[\\/][^\\/]*$/, '') ?? '',
)

const checklist = computed(() => {
  const session = store.session
  if (!session) return '还没有可导出的内容。'
  const parts = session.sheets
    .filter((s) => s.total > 0)
    .map((s) => `${s.name} ${s.done}/${s.total}`)
  return (
    `导出前检查：共 ${session.total} 条记录，${session.done} 条已完整，` +
    `${session.total - session.done} 条仍待补录（将按空缺导出）。` +
    (parts.length ? ` 各工作表：${parts.join('；')}。` : '')
  )
})

async function doExport() {
  exporting.value = true
  const res = await bridge.exportZip()
  if (!res.ok) {
    exporting.value = false
    ElMessage.warning(res.message || '未生成压缩包：没有可安全导出的记录。')
  }
  // 导出结果通过 finished / failed 事件推送，见下方 watch。
}

watch(
  () => store.lastArchive,
  async (path) => {
    if (!exporting.value || !path) return
    exporting.value = false
    const message = store.lastFinalArchive
      ? `导出完成。含全部补录的最终交付包是 template_final.zip；template.zip 是抓取时的初版（不含补录）。\n\n两者都在同一文件夹：\n${finalDir.value}`
      : `template.zip 已生成。\n\n位置：\n${path}`
    await ElMessageBox.alert(message, '导出完成', {
      confirmButtonText: '知道了',
    })
  },
)

watch(
  () => store.statusText,
  (text) => {
    if (exporting.value && text.startsWith('任务失败')) {
      exporting.value = false
      ElMessage.error(text)
    }
  },
)
</script>

<template>
  <section>
    <h1 class="page-title">预览与导出</h1>
    <p class="page-subtitle muted">
      预览就是最终 template.xlsx 的样子（含你的全部人工补录）。确认无误后点「导出
      template.zip」。
    </p>
    <div class="notice-banner">{{ checklist }}</div>

    <div class="card">
      <div class="action-row">
        <el-button
          type="primary"
          size="large"
          :icon="Download"
          :loading="exporting"
          :disabled="!store.session || store.running"
          @click="doExport"
        >
          {{ exporting ? '正在导出…' : '导出 template.zip' }}
        </el-button>
        <el-button
          :icon="Grid"
          :disabled="!store.session"
          @click="store.openSheetDialog('preview')"
        >
          预览表格
        </el-button>
        <el-button :icon="Download" :disabled="!store.lastArchive" @click="bridge.downloadJobZip()">
          下载 template.zip
        </el-button>
        <el-button :disabled="!store.session" @click="store.manualEntryDialogOpen = true">
          未收录清单
        </el-button>
      </div>
      <p v-if="store.lastFinalArchive" class="muted archive-path">
        ✅ 交付目录：{{ finalDir }}（template.zip 与 template_final.zip 都在此文件夹）
      </p>
      <p v-else-if="store.lastArchive" class="muted archive-path">
        ✅ 最近导出：{{ store.lastArchive }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.action-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.archive-path {
  margin: 12px 0 0;
  word-break: break-all;
}
</style>

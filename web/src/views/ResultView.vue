<script setup lang="ts">
// 第 3 步：抓取结果 — 汇总 + 「预览表格」按钮弹出 WPS 风格表格。
import { Grid } from '@element-plus/icons-vue'
import { computed } from 'vue'

import { useJobStore } from '@/stores/job'

const store = useJobStore()

const sheets = computed(() => store.session?.sheets ?? [])
const totalAttention = computed(() => {
  if (!store.session) return 0
  return store.session.total - store.session.done
})
</script>

<template>
  <section>
    <h1 class="page-title">第 3 步 · 抓取结果</h1>
    <p class="page-subtitle muted">
      结果已经按 template 的 8 张工作表排好：列名、顺序与最终交付表完全一致。
    </p>
    <div class="notice-banner">
      表格最后的「状态 / 待补字段」只是给你看的，不会写进交付包。橙色的「待补录」别担心，
      下一步可以对照原页面人工补齐。
    </div>

    <div class="card" v-if="store.session">
      <div class="summary-row">
        <div class="summary-item">
          <span class="summary-num">{{ store.session.total }}</span>
          <span class="muted">总记录</span>
        </div>
        <div class="summary-item">
          <span class="summary-num ok">{{ store.session.done }}</span>
          <span class="muted">已完整</span>
        </div>
        <div class="summary-item">
          <span class="summary-num warn">{{ totalAttention }}</span>
          <span class="muted">待补录</span>
        </div>
        <el-button
          type="primary"
          :icon="Grid"
          size="large"
          class="preview-btn"
          @click="store.openSheetDialog('preview')"
        >
          预览表格
        </el-button>
      </div>
      <el-divider />
      <div class="sheet-chips">
        <el-tag
          v-for="sheet in sheets"
          :key="sheet.name"
          :type="sheet.total === 0 ? 'info' : sheet.done === sheet.total ? 'success' : 'warning'"
          effect="plain"
        >
          {{ sheet.name }} {{ sheet.done }}/{{ sheet.total }}
        </el-tag>
      </div>
    </div>

    <div class="card" v-else>
      <p class="muted">还没有抓取结果。请先完成抓取，或从欢迎页上传 template.zip 继续补录。</p>
    </div>
  </section>
</template>

<style scoped>
.summary-row {
  display: flex;
  align-items: center;
  gap: 28px;
  flex-wrap: wrap;
}

.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.summary-num {
  font-size: 26px;
  font-weight: 700;
}

.summary-num.ok {
  color: #67c23a;
}
.summary-num.warn {
  color: #e6a23c;
}

.preview-btn {
  margin-left: auto;
}

.sheet-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 991px) {
  .summary-row {
    gap: 18px;
  }

  .preview-btn {
    margin-left: 0;
    width: 100%;
  }
}
</style>

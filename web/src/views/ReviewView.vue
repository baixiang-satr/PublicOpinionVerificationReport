<script setup lang="ts">
// 第 4 步：采集与补录 — 说明 + 「打开补录表格」按钮弹出可编辑 WPS 风格表格。
import { EditPen, Grid } from '@element-plus/icons-vue'
import { computed } from 'vue'

import { useJobStore } from '@/stores/job'

const store = useJobStore()
const sheets = computed(() => (store.session?.sheets ?? []).filter((s) => s.total > 0))
</script>

<template>
  <section>
    <h1 class="page-title">第 4 步 · 采集与补录</h1>
    <p class="page-subtitle muted">
      表格和 template 一模一样：红色空格是必补项，下拉选项与模板完全相同，修改会自动保存。
    </p>
    <div class="notice-banner">
      缺截图：选中记录行后点「截取内容页」或「截取个人页」，在打开的窗口中点「开始框选」，
      框选屏幕上任意区域（含地址栏 URL）即自动保存；
      URL 单元格点击即可打开原页面。群聊 / 朋友圈没有 URL，可以在表格里直接添加手工行。
    </div>

    <div class="card" v-if="store.session">
      <div class="head-row">
        <div>
          <div class="counts">
            共 {{ store.session.total }} 条 · 无需补录 {{ store.session.done }} · 待补录
            <span class="warn">{{ store.session.total - store.session.done }}</span>
          </div>
          <div class="muted tip">
            补录在弹出的表格窗口中进行，操作方式与 WPS 表格一致。
          </div>
        </div>
        <el-button
          type="primary"
          size="large"
          :icon="EditPen"
          @click="store.openSheetDialog('edit')"
        >
          打开补录表格
        </el-button>
      </div>
      <el-divider />
      <div class="sheet-chips">
        <el-tag
          v-for="sheet in sheets"
          :key="sheet.name"
          :type="sheet.done === sheet.total ? 'success' : 'warning'"
          effect="plain"
        >
          {{ sheet.name }} {{ sheet.done }}/{{ sheet.total }}
        </el-tag>
      </div>
    </div>

    <div class="card" v-else>
      <p class="muted">
        请先完成抓取，或从欢迎页上传 template.zip 继续补录。
      </p>
      <el-button :icon="Grid" disabled>打开补录表格</el-button>
    </div>
  </section>
</template>

<style scoped>
.head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.counts {
  font-size: 15px;
  font-weight: 600;
}

.warn {
  color: #e6a23c;
}

.tip {
  margin-top: 4px;
}

.sheet-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>

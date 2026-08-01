<script setup lang="ts">
// 欢迎页：两种开始方式（新建 / 上传 zip 补录）。
import { Upload, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'

const store = useJobStore()

async function importZip() {
  const result = await bridge.pickZipFile()
  if (result.ok) {
    await store.refreshSession()
    ElMessage.success('template.zip 已导入，可以直接补录。')
    store.goTo(4)
  } else if (result.message) {
    ElMessage.warning(result.message)
  }
}
</script>

<template>
  <section>
    <h1 class="page-title">欢迎使用舆情验证报告工具</h1>
    <p class="page-subtitle muted">
      批量读取网页，自动整理证据，生成固定格式的 template.zip 交付包。
    </p>
    <div class="notice-banner">
      第一次使用？选择「新建采集任务」，跟着左侧步骤一步步来即可；
      已经生成过 template.zip，想继续补录，直接上传它就行。
    </div>

    <div class="mode-grid">
      <button class="mode-card" type="button" @click="store.startNewTask()">
        <el-icon :size="34" color="var(--el-color-primary)"><VideoPlay /></el-icon>
        <span class="mode-title">新建采集任务</span>
        <span class="mode-desc muted">
          准备一个包含网页链接的 TXT、CSV 或 XLSX 文件，工具会自动抓取内容、截图并生成
          template.zip。
        </span>
      </button>
      <button class="mode-card" type="button" @click="importZip">
        <el-icon :size="34" color="var(--el-color-primary)"><Upload /></el-icon>
        <span class="mode-title">上传 template.zip 补录</span>
        <span class="mode-desc muted">
          把之前生成的 template.zip 直接传上来，在原有内容基础上继续人工补录、补截图，再重新导出。
        </span>
      </button>
    </div>
  </section>
</template>

<style scoped>
section {
  width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(360px, 100%), 1fr));
  gap: 14px;
}

.mode-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  min-height: 150px;
  padding: 18px;
  background: var(--poir-card);
  border: 1px solid var(--poir-border);
  border-radius: var(--poir-radius);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition:
    border-color 0.15s,
    box-shadow 0.15s;
  min-width: 0;
  overflow-wrap: anywhere;
}

.mode-card:hover {
  border-color: var(--el-color-primary);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.mode-title {
  font-size: 15px;
  font-weight: 600;
}

.mode-desc {
  line-height: 1.6;
}

@media (max-width: 1399px) {
  .mode-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .mode-card {
    min-height: 0;
  }
}
</style>

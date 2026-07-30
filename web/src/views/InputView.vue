<script setup lang="ts">
// 第 1 步：选择 URL 文件 + 运行参数 + 登录态管理入口。
import { FolderOpened, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'

const store = useJobStore()

async function pickFile() {
  const info = await bridge.pickInputFile()
  if (!info) return
  store.inputPath = info.path
  store.urlCount = info.url_count
  if ((info as { error?: string }).error) {
    ElMessage.warning((info as { error?: string }).error ?? '文件读取失败。')
    return
  }
  if (info.url_count === 0) {
    ElMessage.warning('文件中没有可处理的 HTTP(S) URL。')
  } else {
    ElMessage.success(
      `已读取 ${info.url_count} 条有效 URL` +
        (info.rejected_count ? `，${info.rejected_count} 条无效已忽略。` : '。'),
    )
  }
}

function openAuth() {
  store.authDialogOpen = true
  void store.refreshAuth()
}
</script>

<template>
  <section>
    <h1 class="page-title">第 1 步 · 选择 URL 文件并确认参数</h1>
    <p class="page-subtitle muted">
      文件里只要出现 http(s) 链接即可，重复链接会自动去重。
    </p>
    <div class="notice-banner">
      不确定参数就保持默认。首次使用建议先点「管理平台登录态」，验证哪些平台游客可访问，
      受限平台人工登录一次即可长期使用。
    </div>

    <div class="card">
      <h2 class="card-title">URL 文件</h2>
      <p class="card-desc muted">支持 TXT / CSV / XLSX，自动提取其中的 http(s) 链接。</p>
      <div class="file-row">
        <el-button :icon="FolderOpened" @click="pickFile">选择文件…</el-button>
        <span v-if="store.inputPath" class="file-path">{{ store.inputPath }}</span>
        <span v-else class="muted">尚未选择文件</span>
      </div>
      <div v-if="store.urlCount > 0" class="muted file-meta">
        已识别 {{ store.urlCount }} 条有效 URL
      </div>
    </div>

    <div class="card" v-if="store.options">
      <h2 class="card-title">运行参数</h2>
      <p class="card-desc muted">不懂就保持默认。</p>
      <el-form label-width="110px" label-position="left" class="options-form">
        <el-form-item label="同时处理">
          <el-input-number v-model="store.options.max_concurrency" :min="1" :max="10" />
          <span class="muted form-hint">数值越大越快，但会增加电脑负担和反爬风险，建议 3~5。</span>
        </el-form-item>
        <el-form-item label="单页超时">
          <el-input-number
            v-model="store.options.page_timeout_seconds"
            :min="5"
            :max="180"
          />
          <span class="muted form-hint">秒。超过仍未加载完成的页面视为超时。</span>
        </el-form-item>
        <el-form-item label="失败重试">
          <el-input-number v-model="store.options.max_retries" :min="0" :max="5" />
          <span class="muted form-hint">次。0 表示出错就跳过。</span>
        </el-form-item>
        <el-form-item label="截图格式">
          <el-radio-group v-model="store.options.screenshot_format">
            <el-radio-button value="jpeg">JPG（体积更小）</el-radio-button>
            <el-radio-button value="png">PNG（文字更清晰）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="浏览器模式">
          <el-checkbox v-model="store.options.headless">
            后台运行浏览器（无界面模式）
          </el-checkbox>
        </el-form-item>
      </el-form>
      <div class="auth-row">
        <el-button :icon="Lock" @click="openAuth">管理平台登录态…</el-button>
        <span class="muted">先验证游客访问，只对确实受限的平台人工登录。</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.file-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.file-path {
  font-size: 13px;
  word-break: break-all;
}

.file-meta {
  margin-top: 8px;
}

.options-form {
  max-width: 720px;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
  line-height: 1.4;
}

.auth-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 6px;
}

@media (max-width: 991px) {
  .options-form :deep(.el-form-item__label) {
    width: 92px !important;
  }
}
</style>

<script setup lang="ts">
// 抓取中登录态失效引导 — 后端逐个平台弹出登录窗口（一次一个），
// 本弹窗只负责引导：完成登录并保存 / 重试 / 跳过该平台。
import { computed } from 'vue'
import { ElMessage } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'

const store = useJobStore()
const relogin = computed(() => store.relogin)
const waiting = computed(() => relogin.value?.phase === 'waiting')

async function confirm() {
  if (!relogin.value) return
  const result = await bridge.authConfirm(relogin.value.key)
  if (result.ok) ElMessage.info(result.message)
  else ElMessage.warning(result.message)
}

async function decide(action: 'skip' | 'retry') {
  if (!relogin.value) return
  const result = await bridge.authResumeLogin(relogin.value.key, action)
  if (!result.ok) ElMessage.warning(result.message)
}
</script>

<template>
  <el-dialog
    :model-value="!!relogin"
    title="需要重新登录"
    width="min(520px, 92vw)"
    :show-close="false"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
  >
    <div v-if="relogin" class="relogin-body">
      <p class="relogin-platform">{{ relogin.name }}</p>
      <p class="muted">{{ relogin.message }}</p>
      <p v-if="waiting" class="muted">
        在弹出的浏览器窗口中完成登录、扫码或验证码后，回到这里点击“完成登录并保存”。
      </p>
    </div>
    <template #footer>
      <template v-if="waiting">
        <el-button @click="decide('skip')">跳过该平台</el-button>
        <el-button type="primary" @click="confirm">完成登录并保存</el-button>
      </template>
      <template v-else>
        <el-button @click="decide('skip')">跳过该平台</el-button>
        <el-button type="primary" @click="decide('retry')">重试登录</el-button>
      </template>
    </template>
  </el-dialog>
</template>

<style scoped>
.relogin-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.relogin-platform {
  font-size: 16px;
  font-weight: 600;
}
</style>

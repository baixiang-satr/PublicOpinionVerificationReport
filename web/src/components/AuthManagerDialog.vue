<script setup lang="ts">
// 登录态管理中心 — 与 PyQt 版一致的卡片式布局：
// 每个平台一张卡片（状态点 + 名称 + 状态 + 账号 + 说明 + 验证/登录/退出按钮）。
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'
import type { AuthPlatform } from '@/types'

const visible = defineModel<boolean>({ required: true })
const store = useJobStore()
const probingAll = ref(false)
const busyKeys = ref<Set<string>>(new Set())

const platforms = computed(() => store.authPlatforms)

watch(visible, async (open) => {
  if (open) await store.refreshAuth()
})

function isBusy(key: string) {
  return busyKeys.value.has(key)
}

function setBusy(key: string, busy: boolean) {
  const next = new Set(busyKeys.value)
  if (busy) next.add(key)
  else next.delete(key)
  busyKeys.value = next
}

async function probeAll() {
  probingAll.value = true
  try {
    await bridge.authProbeAll()
  } finally {
    // 状态通过 auth 事件逐个推送；这里兜底延时解锁
    window.setTimeout(() => (probingAll.value = false), 1500)
  }
}

async function probe(platform: AuthPlatform) {
  setBusy(platform.key, true)
  try {
    await bridge.authProbe(platform.key)
  } finally {
    window.setTimeout(() => setBusy(platform.key, false), 1200)
  }
}

async function login(platform: AuthPlatform) {
  setBusy(platform.key, true)
  try {
    await bridge.authLogin(platform.key)
    ElMessage.info('已打开平台官方页面，请在浏览器窗口中完成登录；完成后工具会自动复验。')
  } finally {
    window.setTimeout(() => setBusy(platform.key, false), 1200)
  }
}

async function logout(platform: AuthPlatform) {
  await ElMessageBox.confirm(
    `确定删除本机保存的「${platform.name}」登录态吗？`,
    '退出登录',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  await bridge.authLogout(platform.key)
  await store.refreshAuth()
  ElMessage.success(`已退出「${platform.name}」。`)
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="登录态管理中心"
    width="min(920px, 94vw)"
    top="4vh"
    class="auth-dialog"
  >
    <div class="notice-banner">
      先点「验证」检查游客能否访问；显示「需要登录」的平台，点「登录 / 更新」
      在打开的官方页面里人工完成登录（含扫码、验证码），工具复验通过后会加密保存登录态。
    </div>

    <div class="platform-list">
      <div v-for="platform in platforms" :key="platform.key" class="platform-card">
        <span class="status-dot" :class="platform.tone"></span>
        <div class="platform-main">
          <div class="platform-head">
            <span class="platform-name">{{ platform.name }}</span>
            <span class="muted">{{ platform.status_text }}</span>
            <span v-if="platform.account" class="muted account">（{{ platform.account }}）</span>
          </div>
          <div class="muted platform-msg">{{ platform.message }}</div>
        </div>
        <div class="platform-actions">
          <el-button size="small" :loading="isBusy(platform.key)" @click="probe(platform)">
            验证
          </el-button>
          <el-button
            size="small"
            type="primary"
            :loading="isBusy(platform.key)"
            @click="login(platform)"
          >
            登录 / 更新
          </el-button>
          <el-button size="small" plain type="danger" @click="logout(platform)">
            退出登录
          </el-button>
        </div>
      </div>
      <div v-if="platforms.length === 0" class="muted empty">正在加载平台列表…</div>
    </div>

    <template #footer>
      <div class="footer-row">
        <el-button :loading="probingAll" @click="probeAll">全部重新验证</el-button>
        <el-button type="primary" @click="visible = false">完成</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.platform-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 56vh;
  overflow-y: auto;
  padding-right: 4px;
}

.platform-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--poir-border);
  border-radius: var(--poir-radius);
  background: var(--poir-card);
}

.platform-main {
  flex: 1;
  min-width: 0;
}

.platform-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.platform-name {
  font-weight: 600;
}

.account {
  font-size: 12px;
}

.platform-msg {
  margin-top: 2px;
  line-height: 1.5;
  word-break: break-all;
}

.platform-actions {
  display: flex;
  gap: 8px;
  flex: none;
}

.footer-row {
  display: flex;
  justify-content: space-between;
}

.empty {
  text-align: center;
  padding: 24px 0;
}

@media (max-width: 640px) {
  .platform-card {
    flex-wrap: wrap;
  }

  .platform-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>

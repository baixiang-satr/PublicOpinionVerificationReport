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
const busyKeys = ref<Set<string>>(new Set())
const activeStatuses = new Set(['probing', 'waiting_user', 'validating'])

const relevantPlatforms = computed(() => store.authPlatforms.filter((item) => item.relevant))
const otherPlatforms = computed(() => store.authPlatforms.filter((item) => !item.relevant))
const operationActive = computed(() => busyKeys.value.size > 0)

watch(visible, async (open) => {
  if (open) await store.refreshAuth()
})

watch(
  () => store.authPlatforms.map((item) => `${item.key}:${item.status}`).join('|'),
  () => {
    const next = new Set(busyKeys.value)
    for (const key of next) {
      const platform = store.authPlatforms.find((item) => item.key === key)
      if (platform && !activeStatuses.has(platform.status)) next.delete(key)
    }
    busyKeys.value = next
  },
)

function isBusy(key: string) {
  return busyKeys.value.has(key)
}

function setBusy(key: string, busy: boolean) {
  const next = new Set(busyKeys.value)
  if (busy) next.add(key)
  else next.delete(key)
  busyKeys.value = next
}

async function probe(platform: AuthPlatform) {
  setBusy(platform.key, true)
  const result = await bridge.authProbe(platform.key)
  if (!result.ok) {
    setBusy(platform.key, false)
    ElMessage.warning(result.message || '已有登录态操作正在进行。')
  }
}

async function login(platform: AuthPlatform) {
  setBusy(platform.key, true)
  const result = await bridge.authLogin(platform.key)
  if (!result.ok) {
    setBusy(platform.key, false)
    ElMessage.warning(result.message || '已有登录态操作正在进行。')
    return
  }
  ElMessage.info(`已打开「${platform.name}」登录界面；完成后请点击“完成登录并保存”。`)
}

async function confirmLogin(platform: AuthPlatform) {
  const result = await bridge.authConfirm(platform.key)
  if (result.ok) ElMessage.info(result.message)
  else ElMessage.warning(result.message)
}

async function cancelLogin(platform: AuthPlatform) {
  const result = await bridge.authCancel(platform.key)
  if (result.ok) ElMessage.info(result.message)
  else ElMessage.warning(result.message)
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
    :close-on-click-modal="!operationActive"
    :close-on-press-escape="!operationActive"
    :show-close="!operationActive"
  >
    <div class="notice-banner">
      开始抓取前，请确保“本次 URL 涉及的平台”全部显示“登录态有效”。
      请逐个平台点击“登录 / 更新”；每次只打开所选网站的登录界面。
      在网站完成登录后，请回到这里点击“完成登录并保存”。保存后抓取会自动复用。
    </div>

    <div class="platform-list">
      <div v-if="relevantPlatforms.length" class="platform-section-title">本次 URL 涉及的平台</div>
      <div v-for="platform in relevantPlatforms" :key="platform.key" class="platform-card relevant">
        <span class="task-badge">本次任务</span>
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
          <template v-if="platform.status === 'waiting_user' && isBusy(platform.key)">
            <el-button size="small" type="primary" @click="confirmLogin(platform)">完成登录并保存</el-button>
            <el-button size="small" @click="cancelLogin(platform)">取消</el-button>
          </template>
          <template v-else>
          <el-button size="small" :loading="isBusy(platform.key)" :disabled="operationActive && !isBusy(platform.key)" @click="probe(platform)">
            验证
          </el-button>
          <el-button size="small" type="primary" :loading="isBusy(platform.key)" :disabled="operationActive && !isBusy(platform.key)" @click="login(platform)">
            登录 / 更新
          </el-button>
          <el-button size="small" plain type="danger" :disabled="operationActive" @click="logout(platform)">退出登录</el-button>
          </template>
        </div>
      </div>

      <div v-if="otherPlatforms.length" class="platform-section-title secondary">其他平台（本次未涉及）</div>
      <div v-for="platform in otherPlatforms" :key="platform.key" class="platform-card unrelated">
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
          <template v-if="platform.status === 'waiting_user' && isBusy(platform.key)">
            <el-button size="small" type="primary" @click="confirmLogin(platform)">完成登录并保存</el-button>
            <el-button size="small" @click="cancelLogin(platform)">取消</el-button>
          </template>
          <template v-else>
          <el-button size="small" :loading="isBusy(platform.key)" :disabled="operationActive && !isBusy(platform.key)" @click="probe(platform)">
            验证
          </el-button>
          <el-button
            size="small"
            type="primary"
            :loading="isBusy(platform.key)"
            :disabled="operationActive && !isBusy(platform.key)"
            @click="login(platform)"
          >
            登录 / 更新
          </el-button>
          <el-button size="small" plain type="danger" :disabled="operationActive" @click="logout(platform)">
            退出登录
          </el-button>
          </template>
        </div>
      </div>
      <div v-if="store.authPlatforms.length === 0" class="muted empty">正在加载平台列表…</div>
    </div>

    <template #footer>
      <div class="footer-row">
        <span class="muted">一次只处理一个平台，避免多个登录窗口切换。</span>
        <el-button :disabled="operationActive" @click="visible = false">完成</el-button>
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
  gap: 12px;
}

.platform-card.relevant {
  border-color: color-mix(in srgb, var(--el-color-primary) 38%, var(--poir-border));
}

.platform-card.unrelated {
  filter: grayscale(0.72);
  opacity: 0.62;
  background: #f7f7f8;
}

.platform-section-title {
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 6px 2px 4px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font-size: 13px;
  font-weight: 700;
}

.platform-section-title.secondary {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
}

.task-badge {
  flex: none;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 11px;
}

.footer-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
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

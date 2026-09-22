<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'

import AuthManagerDialog from '@/components/AuthManagerDialog.vue'
import ManualEntryDialog from '@/components/ManualEntryDialog.vue'
import ReloginDialog from '@/components/ReloginDialog.vue'
import SheetDialog from '@/components/SheetDialog.vue'
import StepSidebar from '@/components/StepSidebar.vue'
import UrlRecheckDialog from '@/components/UrlRecheckDialog.vue'
import { bridge } from '@/api/bridge'
import { STEPS, useJobStore } from '@/stores/job'

import ActivationView from '@/views/ActivationView.vue'
import CrawlView from '@/views/CrawlView.vue'
import ExportView from '@/views/ExportView.vue'
import InputView from '@/views/InputView.vue'
import ResultView from '@/views/ResultView.vue'
import ReviewView from '@/views/ReviewView.vue'
import WelcomeView from '@/views/WelcomeView.vue'

const store = useJobStore()
const views = [WelcomeView, InputView, CrawlView, ResultView, ReviewView, ExportView]
const activeView = computed(() => views[store.step])

const isNarrow = ref(typeof window !== 'undefined' && window.innerWidth < 992)
function onResize() {
  isNarrow.value = window.innerWidth < 992
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

// U02：窗口关闭被后端否决后弹出三态确认（最小化 / 直接退出 / 取消）
watch(
  () => store.closePrompt,
  async () => {
    try {
      await ElMessageBox.confirm(
        store.running
          ? '抓取任务仍在运行。选择「最小化到任务栏」任务会在后台继续；选择「直接退出」将中断任务。'
          : '要退出舆情验证报告工作台吗？',
        '关闭确认',
        {
          confirmButtonText: '最小化到任务栏',
          cancelButtonText: '直接退出',
          distinguishCancelAndClose: true,
          type: 'warning',
        },
      )
      await bridge.minimizeWindow()
    } catch (action) {
      // cancel=直接退出；close（点 X / Esc）=取消，什么也不做
      if (action === 'cancel') await bridge.confirmExit()
    }
  },
)
</script>

<template>
  <!-- 未激活：整页激活界面，隐藏步骤向导 -->
  <main v-if="store.license && !store.license.activated" class="shell-main activation-main">
    <ActivationView />
  </main>
  <div v-else class="shell">
    <StepSidebar
      class="shell-sidebar"
      :steps="STEPS"
      :active="store.step"
      :furthest="store.furthest"
      :horizontal="isNarrow"
      @select="store.goTo"
    />
    <main class="shell-main">
      <component :is="activeView" />
      <footer class="shell-nav">
        <el-button :disabled="store.step === 0 || store.running" @click="store.back()">
          ← 上一步
        </el-button>
        <span class="muted nav-status">{{ store.statusText }}</span>
        <el-button
          v-if="store.step !== 2"
          type="primary"
          :disabled="!store.canGoNext"
          @click="store.next()"
        >
          {{ store.nextLabel }}
        </el-button>
      </footer>
    </main>
    <AuthManagerDialog v-model="store.authDialogOpen" />
    <ReloginDialog />
    <SheetDialog v-model="store.sheetDialogOpen" :mode="store.sheetDialogMode" />
    <UrlRecheckDialog />
    <ManualEntryDialog />
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.activation-main {
  height: 100%;
  overflow-y: auto;
  padding: 20px 22px 12px;
}

.shell-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 20px 22px 12px;
}

.shell-nav {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0 6px;
  background: var(--poir-canvas);
  border-top: 1px solid var(--poir-border);
  margin-top: auto;
}

.nav-status {
  flex: 1;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 窄屏：侧边步骤条收到顶部，主区域全宽 ── */
@media (max-width: 991px) {
  .shell {
    flex-direction: column;
  }

  .shell-main {
    padding: 12px 12px 8px;
  }
}
</style>

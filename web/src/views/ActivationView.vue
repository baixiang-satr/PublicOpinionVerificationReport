<script setup lang="ts">
// 激活页：未激活时整页展示（隐藏步骤向导）。
// 用户复制机器码发给供应商 → 获得授权码 → 粘贴激活。
import { CopyDocument, Key } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import { useJobStore } from '@/stores/job'

const store = useJobStore()
const code = ref('')
const busy = ref(false)

const license = computed(() => store.license)
const machineCode = computed(() => license.value?.machine_code || '读取中…')

async function copyMachineCode() {
  const text = license.value?.machine_code ?? ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('机器码已复制，请发送给供应商。')
  } catch {
    ElMessage.warning('复制失败，请手动选中复制。')
  }
}

async function activate() {
  const text = code.value.trim()
  if (!text) {
    ElMessage.warning('请先粘贴授权码。')
    return
  }
  busy.value = true
  try {
    const info = await store.activateLicense(text)
    if (info.activated) {
      ElMessage.success(info.message)
      code.value = ''
      await store.bootstrap()
    } else {
      ElMessage.error(info.message)
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="activation">
    <h1 class="page-title">软件授权激活</h1>
    <p class="page-subtitle muted">
      本工具采用一机一码授权。请将本机机器码发送给供应商，获取授权码后在此激活。
    </p>

    <el-alert
      v-if="license && license.status !== 'not_activated'"
      class="status-alert"
      type="warning"
      :title="license.message"
      :closable="false"
      show-icon
    />

    <div class="activation-card">
      <div class="field-label">本机机器码</div>
      <div class="machine-row">
        <code class="machine-code">{{ machineCode }}</code>
        <el-button :icon="CopyDocument" @click="copyMachineCode">复制机器码</el-button>
      </div>

      <div class="field-label">授权码</div>
      <el-input
        v-model="code"
        type="textarea"
        :rows="4"
        placeholder="粘贴供应商提供的授权码（POIR1. 开头）"
        resize="none"
      />

      <div class="action-row">
        <el-button
          type="primary"
          :icon="Key"
          :loading="busy"
          :disabled="!license?.machine_code"
          @click="activate"
        >
          激活
        </el-button>
        <span class="muted hint">
          授权码与机器码绑定，复制到其他电脑无法使用；重装系统后需重新获取授权码。
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.activation {
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
  padding-top: 24px;
}

.status-alert {
  margin-bottom: 14px;
}

.activation-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px;
  background: var(--poir-card);
  border: 1px solid var(--poir-border);
  border-radius: var(--poir-radius);
}

.field-label {
  font-weight: 600;
}

.machine-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.machine-code {
  font-size: 16px;
  letter-spacing: 1px;
  padding: 8px 12px;
  background: var(--poir-canvas);
  border: 1px dashed var(--poir-border);
  border-radius: var(--poir-radius);
  user-select: all;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}

.hint {
  font-size: 12px;
  line-height: 1.6;
}
</style>

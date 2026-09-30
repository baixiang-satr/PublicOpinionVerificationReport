<script setup lang="ts">
// 大模型设置弹窗：OpenAI 兼容接口（base_url + api_key + model）配置与连通性测试。
// 启用后用于抓取兜底：标题/作者/发布时间缺失时，把页面原文发给该接口做结构化
// 提取；返回值必须通过原文校验才会被采用（见 src/llm/extract.py）。
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { bridge } from '@/api/bridge'
import { useJobStore } from '@/stores/job'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const store = useJobStore()
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const enabled = ref(false)
const baseUrl = ref('https://api.openai.com/v1')
const model = ref('')
const apiKey = ref('')
const timeoutSeconds = ref(30)
const maxInputChars = ref(6000)
const hasSavedKey = ref(false)
const maskedKey = ref('')
const saving = ref(false)

const keyPlaceholder = computed(() =>
  hasSavedKey.value ? `已保存（${maskedKey.value}），留空表示不修改` : 'sk-…',
)

watch(visible, async (open) => {
  if (!open) return
  store.llmTesting = false
  store.llmTestResult = null
  const result = await bridge.getLlmSettings()
  const settings = result.settings
  enabled.value = settings.enabled
  baseUrl.value = settings.base_url
  model.value = settings.model
  timeoutSeconds.value = settings.timeout_seconds
  maxInputChars.value = settings.max_input_chars
  hasSavedKey.value = settings.has_api_key
  maskedKey.value = settings.api_key_masked
  apiKey.value = ''
})

async function save(showMessage = true): Promise<boolean> {
  saving.value = true
  try {
    const result = await bridge.saveLlmSettings({
      enabled: enabled.value,
      base_url: baseUrl.value,
      model: model.value,
      timeout_seconds: timeoutSeconds.value,
      max_input_chars: maxInputChars.value,
      api_key: apiKey.value,
    })
    if (!result.ok) {
      ElMessage.error(result.message ?? '保存失败。')
      return false
    }
    if (result.settings) {
      hasSavedKey.value = result.settings.has_api_key
      maskedKey.value = result.settings.api_key_masked
    }
    apiKey.value = ''
    if (showMessage) ElMessage.success('大模型设置已保存。')
    return true
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  if (!(await save(false))) return
  store.llmTesting = true
  store.llmTestResult = null
  const result = await bridge.testLlmConnection()
  if (!result.ok) {
    store.llmTesting = false
    ElMessage.warning(result.message)
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="大模型设置" width="560px" :close-on-click-modal="false">
    <div class="llm-hint muted">
      兼容 OpenAI 接口（DeepSeek、通义、Kimi 等均可）。启用后仅用于抓取兜底：
      当标题、作者或发布时间缺失时，把页面原文发给该接口做结构化提取，
      返回值必须通过原文校验才会被采用，不会凭空生成内容。
    </div>
    <el-form label-width="96px" class="llm-form">
      <el-form-item label="启用">
        <el-switch v-model="enabled" active-text="启用提取兜底" />
      </el-form-item>
      <el-form-item label="接口地址">
        <el-input v-model="baseUrl" placeholder="https://api.openai.com/v1" clearable />
      </el-form-item>
      <el-form-item label="模型">
        <el-input v-model="model" placeholder="如 deepseek-chat / qwen-plus / gpt-4o-mini" clearable />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input
          v-model="apiKey"
          type="password"
          show-password
          :placeholder="keyPlaceholder"
          clearable
        />
      </el-form-item>
      <el-form-item label="超时时间">
        <el-input-number v-model="timeoutSeconds" :min="5" :max="120" />
        <span class="muted form-hint">秒。</span>
      </el-form-item>
      <el-form-item label="输入截断">
        <el-input-number v-model="maxInputChars" :min="500" :max="20000" :step="500" />
        <span class="muted form-hint">字。发给模型的原文上限，越大越准但费用越高。</span>
      </el-form-item>
    </el-form>
    <el-alert
      v-if="store.llmTestResult"
      class="llm-result"
      :type="store.llmTestResult.ok ? 'success' : 'error'"
      :closable="false"
      show-icon
    >
      <template #title>
        {{ store.llmTestResult.message }}
        <span v-if="store.llmTestResult.latency_ms" class="muted">
          （{{ store.llmTestResult.latency_ms }} ms{{ store.llmTestResult.reply ? `，回复：${store.llmTestResult.reply}` : '' }}）
        </span>
      </template>
    </el-alert>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button :loading="store.llmTesting" @click="testConnection">测试连接</el-button>
      <el-button type="primary" :loading="saving" @click="save()">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.llm-hint {
  margin-bottom: 14px;
  line-height: 1.6;
}

.llm-form :deep(.form-hint) {
  margin-left: 8px;
  font-size: 12px;
}

.llm-result {
  margin-top: 4px;
}
</style>

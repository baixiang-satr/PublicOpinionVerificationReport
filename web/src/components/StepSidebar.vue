<script setup lang="ts">
// 竖向节点步骤条：窄条、不占篇幅；窄屏时折叠为顶部横向紧凑步骤。
import { computed } from 'vue'

interface StepItem {
  title: string
  desc: string
}

const props = defineProps<{
  steps: readonly StepItem[]
  active: number
  furthest: number
  horizontal: boolean
}>()

const emit = defineEmits<{ select: [index: number] }>()

const items = computed(() =>
  props.steps.map((step, index) => ({
    ...step,
    index,
    status:
      index < props.active ? 'finish' : index === props.active ? 'process' : 'wait',
    clickable: index <= props.furthest,
  })),
)
</script>

<template>
  <aside v-if="!horizontal" class="sidebar">
    <div class="brand">
      <span class="brand-mark">舆</span>
      <span class="brand-name">舆情验证</span>
    </div>
    <el-steps direction="vertical" :active="active" class="steps">
      <el-step
        v-for="item in items"
        :key="item.index"
        :status="item.status"
        :class="{ clickable: item.clickable }"
        @click="item.clickable && emit('select', item.index)"
      >
        <template #title>
          <span class="step-title" :class="{ current: item.index === active }">
            {{ item.title }}
          </span>
        </template>
        <template #description>
          <span class="step-desc">{{ item.desc }}</span>
        </template>
      </el-step>
    </el-steps>
  </aside>

  <header v-else class="topbar">
    <span class="brand-name">舆情验证</span>
    <el-steps :active="active" align-center class="steps-h">
      <el-step
        v-for="item in items"
        :key="item.index"
        :status="item.status"
        :title="item.title"
        :class="{ clickable: item.clickable }"
        @click="item.clickable && emit('select', item.index)"
      />
    </el-steps>
  </header>
</template>

<style scoped>
.sidebar {
  width: var(--poir-sidebar-w);
  flex: none;
  display: flex;
  flex-direction: column;
  background: var(--poir-card);
  border-right: 1px solid var(--poir-border);
  padding: 10px 10px;
  /* 内容正常情况下放得下，不出滚动条；窗口过矮时才滚动 */
  overflow-y: auto;
}

.brand {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 6px 10px;
  border-bottom: 1px solid var(--poir-border);
  margin-bottom: 10px;
}

.brand-mark {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.brand-name {
  font-weight: 600;
  font-size: 14px;
}

.steps :deep(.el-step) {
  cursor: default;
}

/* el-steps--vertical 默认 height:100%，与 brand 叠加会把内容顶出滚动条；
   min-height: max-content 保证窗口过矮时步骤不挤压、改由侧栏滚动 */
.steps {
  flex: 1;
  min-height: max-content;
  height: auto;
}

.steps :deep(.el-step.clickable) {
  cursor: pointer;
}

.step-title {
  font-size: 13px;
}

.step-title.current {
  font-weight: 600;
  color: var(--el-color-primary);
}

.step-desc {
  font-size: 11px;
  color: var(--poir-muted);
}

.steps :deep(.el-step__description) {
  padding-right: 2px;
}

/* 窄屏顶部横向模式 */
.topbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--poir-card);
  border-bottom: 1px solid var(--poir-border);
  padding: 8px 12px;
  overflow-x: auto;
}

.topbar .brand-name {
  flex: none;
}

.steps-h {
  flex: 1;
  min-width: 0;
}

.steps-h :deep(.el-step__title) {
  font-size: 12px;
  line-height: 1.2;
}

.steps-h :deep(.el-step__head) {
  padding-right: 4px;
}

.steps-h :deep(.el-step.clickable) {
  cursor: pointer;
}
</style>

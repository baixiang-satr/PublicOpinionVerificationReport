<script setup lang="ts">
// 表格弹窗：WPS 风格（Univer 电子表格）。
// - preview 模式：只读预览，链接可点击跳转系统浏览器
// - edit 模式：人工补录，编辑即保存；下拉选项与模板一致；支持截图上传/手工行
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { LocaleType, mergeLocales } from '@univerjs/presets'
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import UniverPresetSheetsCoreZhCN from '@univerjs/preset-sheets-core/locales/zh-CN'
import { UniverSheetsDataValidationPreset } from '@univerjs/preset-sheets-data-validation'
import UniverPresetSheetsDataValidationZhCN from '@univerjs/preset-sheets-data-validation/locales/zh-CN'
import { UniverSheetsHyperLinkPreset } from '@univerjs/preset-sheets-hyper-link'
import UniverPresetSheetsHyperLinkZhCN from '@univerjs/preset-sheets-hyper-link/locales/zh-CN'
import { createUniver } from '@univerjs/presets'
import type { FWorkbook, FWorksheet } from '@univerjs/preset-sheets-core'
import { SetRangeValuesMutation, type ISetRangeValuesMutationParams } from '@univerjs/sheets'

import '@univerjs/preset-sheets-core/lib/index.css'
import '@univerjs/preset-sheets-data-validation/lib/index.css'
import '@univerjs/preset-sheets-hyper-link/lib/index.css'

import { bridge } from '@/api/bridge'
import { buildCell, buildWorkbookData, MISSING_COL, STATUS_COL, type SheetModel } from '@/components/sheetModel'
import type { SheetPayload } from '@/types'

const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ mode: 'preview' | 'edit' }>()
const emit = defineEmits<{ changed: [] }>()

type UniverAPI = ReturnType<typeof createUniver>['univerAPI']

/**
 * 隐藏工作表标签右键菜单中的结构性操作：8 张固定模板表不允许增删/改名/隐藏。
 * 保留 show-menu-list（全部工作表导航）可用。
 */
const SHEET_BAR_HIDDEN_MENU = {
  'sheet.command.remove-sheet-confirm': { hidden: true }, // 删除工作表
  'sheet.command.copy-sheet': { hidden: true }, // 复制工作表
  'sheet.operation.rename-sheet': { hidden: true }, // 重命名
  'sheet.command.set-tab-color': { hidden: true }, // 标签颜色
  'sheet.command.set-worksheet-hidden': { hidden: true }, // 隐藏工作表
  'sheet.command.add-range-protection-from-sheet-bar': { hidden: true },
  'sheet.command.delete-worksheet-protection-from-sheet-bar': { hidden: true },
  'sheet.command.change-sheet-protection-from-sheet-bar': { hidden: true },
  'sheet.command.view-sheet-permission-from-sheet-bar': { hidden: true },
}

const containerRef = ref<HTMLElement>()
const loading = ref(false)
const payload = ref<SheetPayload[]>([])
const models = ref<SheetModel[]>([])
const attentionOnly = ref(false)
const batchType = ref('')
const manualSheet = ref('')
const shotPreview = ref<{ url: string; name: string } | null>(null)

let univerInstance: ReturnType<typeof createUniver> | null = null
let applying = false // 自我写入时屏蔽 CommandExecuted 回环

const title = computed(() => (props.mode === 'edit' ? '采集与补录' : '表格预览（只读）'))
const editable = computed(() => props.mode === 'edit')
const manualSheets = computed(() => payload.value.filter((s) => s.manual_row_allowed))
const stats = computed(() => {
  let done = 0
  let total = 0
  for (const sheet of payload.value) {
    total += sheet.rows.length
    done += sheet.rows.filter((r) => !r.attention).length
  }
  return { done, total }
})

// ── Univer 生命周期 ────────────────────────────────────────────────────────
function disposeGrid() {
  univerInstance?.univer.dispose()
  univerInstance = null
}

async function buildGrid() {
  disposeGrid()
  await nextTick()
  const container = containerRef.value
  if (!container) return
  const { workbook, models: built } = buildWorkbookData(payload.value)
  models.value = built

  univerInstance = createUniver({
    locale: LocaleType.ZH_CN,
    locales: {
      [LocaleType.ZH_CN]: mergeLocales(
        UniverPresetSheetsCoreZhCN,
        UniverPresetSheetsDataValidationZhCN,
        UniverPresetSheetsHyperLinkZhCN,
      ),
    },
    presets: [
      UniverSheetsCorePreset({
        container,
        formulaBar: false,
        footer: {
          // sheetBar 是底部工作表标签栏：8 张固定表的切换入口，必须显示
          sheetBar: true,
          statisticBar: false,
          menus: false,
          zoomSlider: false,
          addSheetButtonConfig: { show: false },
        },
        menu: SHEET_BAR_HIDDEN_MENU,
        disableAutoFocus: true,
      }),
      UniverSheetsDataValidationPreset({ showEditOnDropdown: false }),
      UniverSheetsHyperLinkPreset({
        urlHandler: {
          navigateToOtherWebsite: (url: string) => void bridge.openUrl(url),
        },
      }),
    ],
  })
  const { univerAPI } = univerInstance
  univerAPI.createWorkbook(workbook)
  if (import.meta.env.DEV) {
    // 开发/联调辅助：浏览器控制台可直接访问 facade API
    ;(window as unknown as Record<string, unknown>).__univerAPI = univerAPI
  }
  await decorate(univerAPI)
  listenEdits(univerAPI)
  if (attentionOnly.value) applyAttentionFilter()
}

/** 下拉验证 + 超链接。 */
async function decorate(univerAPI: UniverAPI) {
  const workbook = univerAPI.getActiveWorkbook()
  if (!workbook) return
  for (const model of models.value) {
    const sheet = workbook.getSheetBySheetId(model.sheetId)
    if (!sheet) continue
    model.columns.forEach((column, columnIndex) => {
      if (editable.value && column.editable && column.choices.length > 0) {
        const rule = univerAPI
          .newDataValidation()
          .requireValueInList(column.choices, false, true)
          .build()
        sheet.getRange(1, columnIndex, Math.max(model.sheet.rows.length, 1), 1).setDataValidation(rule)
      }
    })
    // 超链接：URL 列且值以 http 开头
    const urlColumns = model.columns
      .map((column, index) => ({ column, index }))
      .filter(({ column }) => column.kind === 'url')
    for (const { index: columnIndex } of urlColumns) {
      for (let rowIndex = 0; rowIndex < model.sheet.rows.length; rowIndex += 1) {
        const row = model.sheet.rows[rowIndex]
        const url = row.cells[model.columns[columnIndex].key] ?? ''
        if (url.startsWith('http')) {
          await sheet.getRange(rowIndex + 1, columnIndex).setHyperLink(url, url)
        }
      }
    }
  }
}

// ── 编辑回写 ───────────────────────────────────────────────────────────────
function listenEdits(univerAPI: UniverAPI) {
  univerAPI.addEvent(univerAPI.Event.CommandExecuted, (event) => {
    if (applying || event.id !== SetRangeValuesMutation.id) return
    const params = event.params as ISetRangeValuesMutationParams
    const model = models.value.find((m) => m.sheetId === params.subUnitId)
    const workbook = univerAPI.getActiveWorkbook()
    const sheet = model ? workbook?.getSheetBySheetId(model.sheetId) : null
    if (!model || !sheet) return
    const cellValue = params.cellValue as Record<string, Record<string, { v?: unknown }>>
    for (const [rowKey, columns] of Object.entries(cellValue)) {
      const row = Number(rowKey)
      for (const [colKey, cell] of Object.entries(columns)) {
        const col = Number(colKey)
        const payloadRow = model.rowAt(row)
        const column = model.columns[col]
        if (!payloadRow || !column) continue
        const value = String((cell as { v?: unknown })?.v ?? '')
        if (!editable.value || !column.editable || !column.field) {
          revertCell(sheet, model, row, col)
          continue
        }
        void submitEdit(sheet, model, payloadRow.eid, column.field, column.key, row, col, value)
      }
    }
  })
}

function revertCell(sheet: FWorksheet, model: SheetModel, row: number, col: number) {
  const payloadRow = model.rowAt(row)
  const column = model.columns[col]
  if (!payloadRow || !column) return
  applying = true
  sheet.getRange(row, col).setValue(buildCell(payloadRow, column))
  applying = false
}

interface EditRowDelta {
  missing: string[]
  attention: boolean
  status_text: string
}

async function submitEdit(
  sheet: FWorksheet,
  model: SheetModel,
  eid: number,
  field: string,
  columnKey: string,
  row: number,
  col: number,
  value: string,
) {
  const result = (await bridge.applyEdit(eid, field, value)) as unknown as {
    ok: boolean
    row?: EditRowDelta
  }
  const payloadRow = model.rowAt(row)
  if (!result.ok || !payloadRow) return
  payloadRow.cells[columnKey] = value
  if (result.row) {
    payloadRow.missing = result.row.missing
    payloadRow.attention = result.row.attention
    payloadRow.status_text = result.row.status_text
  }
  applying = true
  const column = model.columns[col]
  sheet.getRange(row, col).setValue(buildCell(payloadRow, column))
  for (const extraKey of [STATUS_COL, MISSING_COL]) {
    const extraCol = model.columns.findIndex((c) => c.key === extraKey)
    if (extraCol >= 0) sheet.getRange(row, extraCol).setValue(buildCell(payloadRow, model.columns[extraCol]))
  }
  applying = false
  emit('changed')
}

// ── 行级刷新（批量操作后保持视图）─────────────────────────────────────────
async function refreshRows() {
  const fresh = await bridge.getSheetPayload()
  const workbook = univerInstance?.univerAPI.getActiveWorkbook()
  if (!workbook) {
    payload.value = fresh
    return
  }
  let structural = false
  fresh.forEach((sheetData, index) => {
    const current = payload.value[index]
    if (!current || current.rows.length !== sheetData.rows.length) structural = true
  })
  if (structural) {
    payload.value = fresh
    await buildGrid()
    return
  }
  applying = true
  fresh.forEach((sheetData, index) => {
    const model = models.value[index]
    const sheet = workbook.getSheetBySheetId(model?.sheetId ?? '')
    if (!model || !sheet) return
    sheetData.rows.forEach((freshRow, rowIndex) => {
      const old = payload.value[index].rows[rowIndex]
      payload.value[index].rows[rowIndex] = freshRow
      if (JSON.stringify(old) === JSON.stringify(freshRow)) return
      model.columns.forEach((column, columnIndex) => {
        sheet.getRange(rowIndex + 1, columnIndex).setValue(buildCell(freshRow, column))
      })
    })
  })
  applying = false
  emit('changed')
}

// ── 选区定位 ───────────────────────────────────────────────────────────────
function activeCell(): { model: SheetModel; row: number; eid: number } | null {
  const workbook = univerInstance?.univerAPI.getActiveWorkbook()
  const range = workbook?.getActiveRange()
  if (!workbook || !range) return null
  const sheetId = workbook.getActiveSheet().getSheetId()
  const model = models.value.find((m) => m.sheetId === sheetId)
  if (!model) return null
  const row = range.getRow()
  const payloadRow = model.rowAt(row)
  if (!payloadRow) return null
  return { model, row, eid: payloadRow.eid }
}

async function locateEid(eid: number) {
  const workbook = univerInstance?.univerAPI.getActiveWorkbook()
  if (!workbook) return
  for (const model of models.value) {
    const rowIndex = model.sheet.rows.findIndex((r) => r.eid === eid)
    if (rowIndex < 0) continue
    const sheet = workbook.getSheetBySheetId(model.sheetId)
    if (!sheet) return
    if (attentionOnly.value) {
      attentionOnly.value = false
      applyAttentionFilter()
    }
    workbook.setActiveSheet(sheet)
    await nextTick()
    sheet.getRange(rowIndex + 1, 0).activate()
    return
  }
}

// ── 工具栏操作 ─────────────────────────────────────────────────────────────
async function jumpAttention(backwards: boolean) {
  const current = activeCell()?.eid ?? 0
  const { eid } = await bridge.nextAttention(current, backwards)
  if (eid === null) {
    ElMessage.success('全部完成，没有待补录的记录了。')
    return
  }
  await locateEid(eid)
}

async function copyFromPrevious() {
  const active = activeCell()
  if (!active) {
    ElMessage.info('请先选中一条记录。')
    return
  }
  const { copied } = await bridge.copyFromPrevious(active.eid)
  if (copied > 0) {
    ElMessage.success(`已复制 ${copied} 个字段`)
    await refreshRows()
  } else {
    ElMessage.info('没有可复制的空字段。')
  }
}

async function applyBatchType() {
  if (!batchType.value) return
  const workbook = univerInstance?.univerAPI.getActiveWorkbook()
  const range = workbook?.getActiveRange()
  if (!workbook || !range) {
    ElMessage.info('请先在表格中选中一条或多条记录。')
    return
  }
  const sheetId = workbook.getActiveSheet().getSheetId()
  const model = models.value.find((m) => m.sheetId === sheetId)
  if (!model) return
  const start = range.getRow()
  const end = start + range.getHeight() - 1
  const eids: number[] = []
  for (let row = start; row <= end; row += 1) {
    const payloadRow = model.rowAt(row)
    if (payloadRow) eids.push(payloadRow.eid)
  }
  if (eids.length === 0) {
    ElMessage.info('请选中至少一条记录行。')
    return
  }
  const { skipped } = await bridge.batchTextType(eids, batchType.value)
  await refreshRows()
  if (skipped > 0) ElMessage.warning(`${skipped} 条记录的工作表不允许该文本类型，已跳过。`)
  else ElMessage.success('已应用。')
  batchType.value = ''
}

async function addManualRow() {
  if (!manualSheet.value) return
  const { eid } = await bridge.addManualRow(manualSheet.value)
  if (eid === null) return
  payload.value = await bridge.getSheetPayload()
  await buildGrid()
  await locateEid(eid)
  ElMessage.success(`已添加手工行 ${String(eid).padStart(3, '0')}`)
}

async function removeManualRow() {
  const active = activeCell()
  if (!active) {
    ElMessage.info('请先选中要删除的手工行。')
    return
  }
  const row = active.model.rowAt(active.row)
  if (!row?.manual) {
    ElMessage.warning('只能删除手工行（无 URL 的行）。')
    return
  }
  await ElMessageBox.confirm(
    `确定删除记录 ${String(row.eid).padStart(3, '0')} 吗？它的人工填写内容也会一并删除。`,
    '删除手工行',
    { type: 'warning' },
  )
  await bridge.removeManualRow(row.eid)
  payload.value = await bridge.getSheetPayload()
  await buildGrid()
}

async function uploadScreenshot(mode: 'primary' | 'attachment') {
  const active = activeCell()
  if (!active) {
    ElMessage.info('请先选中一条记录。')
    return
  }
  const { ok, name } = await bridge.pickScreenshot(active.eid, mode)
  if (!ok) return
  ElMessage.success(`已保存截图 ${name}`)
  await refreshRows()
}

async function viewScreenshot() {
  const active = activeCell()
  if (!active) {
    ElMessage.info('请先选中一条记录。')
    return
  }
  const { data_url: dataUrl, name } = await bridge.screenshotDataUrl(active.eid)
  if (!dataUrl) {
    ElMessage.info('该记录还没有截图。')
    return
  }
  shotPreview.value = { url: dataUrl, name }
}

async function openCurrentLink() {
  const active = activeCell()
  if (!active) return
  const row = active.model.rowAt(active.row)
  const url = row?.final_url || row?.url
  if (url?.startsWith('http')) {
    await bridge.openUrl(url)
  } else {
    ElMessage.info('该记录没有可打开的链接。')
  }
}

// ── 只看待补录 ─────────────────────────────────────────────────────────────
function applyAttentionFilter() {
  const workbook = univerInstance?.univerAPI.getActiveWorkbook()
  if (!workbook) return
  applying = true
  for (const model of models.value) {
    const sheet = workbook.getSheetBySheetId(model.sheetId)
    if (!sheet) continue
    const rows = model.sheet.rows
    sheet.showRows(1, rows.length)
    if (!attentionOnly.value) continue
    let runStart = -1
    for (let i = 0; i <= rows.length; i += 1) {
      const hide = i < rows.length && !rows[i].attention
      if (hide && runStart < 0) runStart = i
      if ((!hide || i === rows.length) && runStart >= 0) {
        sheet.hideRows(runStart + 1, i - runStart)
        runStart = -1
      }
    }
  }
  applying = false
}

watch(attentionOnly, applyAttentionFilter)

// ── 打开/关闭 ──────────────────────────────────────────────────────────────
watch(visible, async (open) => {
  if (open) {
    loading.value = true
    try {
      payload.value = await bridge.getSheetPayload()
      manualSheet.value = manualSheets.value[0]?.name ?? ''
      await buildGrid()
    } finally {
      loading.value = false
    }
  } else {
    disposeGrid()
    models.value = []
  }
})
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="title"
    class="sheet-dialog"
    :width="'min(1240px, 96vw)'"
    top="3vh"
    :close-on-click-modal="false"
    :z-index="900"
  >
    <div v-if="editable" class="sheet-toolbar">
      <el-button size="small" @click="jumpAttention(true)">◀ 上一条待补</el-button>
      <el-button size="small" @click="jumpAttention(false)">下一条待补 ▶</el-button>
      <el-button size="small" @click="copyFromPrevious">复制上一条空字段</el-button>
      <el-checkbox v-model="attentionOnly" class="toolbar-item">只看待补录</el-checkbox>
      <el-divider direction="vertical" />
      <el-select
        v-model="batchType"
        size="small"
        placeholder="批量文本类型"
        class="toolbar-select"
      >
        <el-option label="正文" value="正文" />
        <el-option label="评论回复" value="评论回复" />
        <el-option label="商家" value="商家" />
      </el-select>
      <el-button size="small" :disabled="!batchType" @click="applyBatchType">应用</el-button>
      <el-divider direction="vertical" />
      <el-button size="small" @click="openCurrentLink">打开链接</el-button>
      <el-button size="small" @click="viewScreenshot">查看截图</el-button>
      <el-button size="small" @click="uploadScreenshot('primary')">上传主截图…</el-button>
      <el-button size="small" @click="uploadScreenshot('attachment')">添加附件…</el-button>
      <el-divider direction="vertical" />
      <el-select v-model="manualSheet" size="small" class="toolbar-select" placeholder="手工行工作表">
        <el-option v-for="s in manualSheets" :key="s.name" :label="s.name" :value="s.name" />
      </el-select>
      <el-button size="small" :disabled="!manualSheet" @click="addManualRow">添加手工行</el-button>
      <el-button size="small" type="danger" plain @click="removeManualRow">删除手工行</el-button>
      <span class="muted toolbar-stats">共 {{ stats.total }} 条 · 已完整 {{ stats.done }}</span>
    </div>
    <div v-else class="sheet-toolbar">
      <span class="muted">
        只读预览，与最终交付表一致；点击蓝色链接可在浏览器中打开原页面。
      </span>
    </div>
    <div v-loading="loading" class="grid-wrap">
      <div ref="containerRef" class="grid-container"></div>
    </div>

    <el-dialog
      :model-value="shotPreview !== null"
      title="截图预览"
      width="min(860px, 92vw)"
      append-to-body
      @update:model-value="shotPreview = null"
    >
      <div v-if="shotPreview" class="shot-preview">
        <div class="muted">{{ shotPreview.name }}</div>
        <img :src="shotPreview.url" alt="截图预览" />
      </div>
    </el-dialog>
  </el-dialog>
</template>

<style scoped>
.sheet-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.toolbar-select {
  width: 130px;
}

.toolbar-stats {
  margin-left: auto;
}

.grid-wrap {
  height: 66vh;
  min-height: 320px;
  border: 1px solid var(--poir-border);
  border-radius: 6px;
  overflow: hidden;
}

.grid-container {
  width: 100%;
  height: 100%;
}

.shot-preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.shot-preview img {
  max-width: 100%;
  border: 1px solid var(--poir-border);
  border-radius: 4px;
}

@media (max-width: 991px) {
  .grid-wrap {
    height: 58vh;
  }

  .toolbar-select {
    width: 110px;
  }
}
</style>

<style>
/* 弹窗内容区域铺满，贴近 WPS 全屏表格体验 */
.sheet-dialog .el-dialog__body {
  padding-top: 10px;
}

@media (max-width: 640px) {
  .sheet-dialog {
    --el-dialog-width: 100vw !important;
  }
}
</style>

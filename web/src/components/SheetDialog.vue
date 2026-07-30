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
import {
  buildCell,
  buildWorkbookData,
  cellDisplayValue,
  MISSING_COL,
  STATUS_COL,
  type SheetModel,
} from '@/components/sheetModel'
import { useJobStore } from '@/stores/job'
import type { SheetPayload, ScreenshotPair } from '@/types'

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
const manualSheet = ref('')
const shotPreview = ref<ScreenshotPair | null>(null)

// ── 单元格内容查看（选中显示 + 悬停提示；提示限长，不过长）──────────────
const PEEK_INLINE_MAX = 160 // 查看栏内联显示上限
const TIP_MAX = 500 // 提示框上限
const peekText = ref('')
const hoverTip = ref<{ text: string; x: number; y: number } | null>(null)
let hoverTimer: number | undefined
let hoverKey = ''
let lastMouse = { x: 0, y: 0 }

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function cellTextAt(sheetId: string, row: number, col: number): string {
  const model = models.value.find((m) => m.sheetId === sheetId)
  const column = model?.columns[col]
  if (!model || !column) return ''
  if (row === 0) return column.header
  const payloadRow = model.rowAt(row)
  return payloadRow ? cellDisplayValue(payloadRow, column) : ''
}

function hideHoverTip() {
  window.clearTimeout(hoverTimer)
  hoverKey = ''
  hoverTip.value = null
}

function scheduleHoverTip(sheetId: string, row: number, col: number) {
  const key = `${sheetId}:${row}:${col}`
  if (key === hoverKey) return
  hideHoverTip()
  hoverKey = key
  if (row < 1 || col < 0) return
  hoverTimer = window.setTimeout(() => {
    const text = cellTextAt(sheetId, row, col)
    if (text && hoverKey === key) {
      hoverTip.value = { text: truncate(text, TIP_MAX), x: lastMouse.x, y: lastMouse.y }
    }
  }, 450)
}

function onGridPointerMove(event: PointerEvent) {
  lastMouse = { x: event.clientX, y: event.clientY }
  if (hoverTip.value) hoverTip.value = { ...hoverTip.value, x: event.clientX, y: event.clientY }
}

/** 选中单元格（含键盘移动）后更新查看栏。 */
function listenPeeks(univerAPI: UniverAPI) {
  univerAPI.addEvent(univerAPI.Event.SelectionChanged, (params) => {
    hideHoverTip()
    const selection = params.selections?.[0]
    peekText.value = selection
      ? cellTextAt(params.worksheet.getSheetId(), selection.startRow, selection.startColumn)
      : ''
  })
  univerAPI.addEvent(univerAPI.Event.CellHover, (params) => {
    scheduleHoverTip(params.worksheet.getSheetId(), params.row, params.column)
  })
}

const store = useJobStore()

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
}

/** 下拉验证 + 超链接。 */
async function decorate(univerAPI: UniverAPI) {
  const workbook = univerAPI.getActiveWorkbook()
  if (!workbook) return
  listenPeeks(univerAPI)
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
    workbook.setActiveSheet(sheet)
    await nextTick()
    sheet.getRange(rowIndex + 1, 0).activate()
    return
  }
}

// ── 工具栏操作 ─────────────────────────────────────────────────────────────
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

// ── 截图：两张预览 + 框选截取（FS Capture 式）────────────────────────────
async function viewScreenshots() {
  const active = activeCell()
  if (!active) {
    ElMessage.info('请先选中一条记录。')
    return
  }
  const pair = await bridge.listScreenshots(active.eid)
  if (!pair.content && !pair.author) {
    ElMessageBox.alert(
      '本地没有截图。请点击工具条的「截取内容页」/「截取个人页」，在打开的窗口中点「开始框选」截取屏幕区域。',
      '查看截图',
      { confirmButtonText: '知道了' },
    )
    return
  }
  shotPreview.value = pair
  if (!pair.content || !pair.author) {
    const missingLabel = pair.content ? '个人页' : '内容页'
    ElMessageBox.alert(
      `还缺「${missingLabel}截图」。请选中该行后点击「截取${missingLabel}」补充。`,
      '截图未齐全',
      { confirmButtonText: '知道了' },
    )
  }
}

async function captureRegion(target: 'content' | 'author') {
  const active = activeCell()
  if (!active) {
    ElMessage.info('请先选中一条记录。')
    return
  }
  const result = await bridge.startRegionCapture(active.eid, target)
  if (result.ok) {
    ElMessage.success('已打开截图窗口：浏览到目标内容后点「开始框选」，可截取整个屏幕（含地址栏 URL）。')
    return
  }
  if (result.code === 'no_url') {
    await pickLocalImage(active.eid, target)
    return
  }
  ElMessage.warning(result.message || '无法打开截图窗口。')
}

async function pickLocalImage(eid: number, target: 'content' | 'author') {
  const label = target === 'content' ? '内容页' : '个人页'
  try {
    await ElMessageBox.confirm(
      `该行没有链接，无法打开页面截图。是否从本地选择${label}截图图片？`,
      '没有链接',
      { confirmButtonText: '选择图片…', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    return
  }
  const { ok, name } = await bridge.pickScreenshot(eid, target === 'content' ? 'primary' : 'author')
  if (!ok) return
  ElMessage.success(`已保存截图 ${name}`)
  await refreshRows()
}

// ── 截图窗口结果回写 ───────────────────────────────────────────────────────
watch(
  () => store.lastCapture,
  async (capture) => {
    if (!capture) return
    store.lastCapture = null
    if (capture.status === 'saved') {
      ElMessage.success(`已保存截图 ${capture.name}`)
    } else if (capture.status === 'error') {
      ElMessage.error(capture.message || '截图失败。')
    }
    if (visible.value && capture.status === 'saved') await refreshRows()
  },
)

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
    hideHoverTip()
    peekText.value = ''
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
      <el-button size="small" @click="viewScreenshots">查看截图</el-button>
      <el-button size="small" type="primary" plain @click="captureRegion('content')">截取内容页</el-button>
      <el-button size="small" type="primary" plain @click="captureRegion('author')">截取个人页</el-button>
      <el-divider direction="vertical" />
      <el-select v-model="manualSheet" size="small" class="toolbar-select" placeholder="手工行工作表">
        <el-option v-for="s in manualSheets" :key="s.name" :label="s.name" :value="s.name" />
      </el-select>
      <el-button size="small" :disabled="!manualSheet" @click="addManualRow">添加手工行</el-button>
      <el-button size="small" type="danger" plain @click="removeManualRow">删除手工行</el-button>
      <span class="muted toolbar-hint">URL 单元格点击即可打开原页面</span>
      <span class="muted toolbar-stats">共 {{ stats.total }} 条 · 已完整 {{ stats.done }}</span>
    </div>
    <div v-else class="sheet-toolbar">
      <span class="muted">
        只读预览，与最终交付表一致；点击蓝色链接可在浏览器中打开原页面。
      </span>
    </div>
    <div class="cell-peek">
      <span class="peek-label">单元格内容</span>
      <el-tooltip :disabled="!peekText" placement="top" :show-after="150">
        <template #content>
          <span class="peek-tooltip">{{ truncate(peekText, TIP_MAX) }}</span>
        </template>
        <span class="peek-value" :class="{ muted: !peekText }">
          {{ truncate(peekText, PEEK_INLINE_MAX) || '（点击任意单元格查看完整内容）' }}
        </span>
      </el-tooltip>
    </div>
    <div v-loading="loading" class="grid-wrap" @pointermove="onGridPointerMove" @pointerleave="hideHoverTip">
      <div ref="containerRef" class="grid-container"></div>
    </div>
    <teleport to="body">
      <div
        v-if="hoverTip"
        class="cell-hover-tip"
        :style="{ left: `${hoverTip.x + 14}px`, top: `${hoverTip.y + 16}px` }"
      >
        {{ hoverTip.text }}
      </div>
    </teleport>

    <el-dialog
      :model-value="shotPreview !== null"
      title="截图预览"
      width="min(1080px, 94vw)"
      append-to-body
      @update:model-value="shotPreview = null"
    >
      <div v-if="shotPreview" class="shot-pair">
        <figure v-if="shotPreview.content" class="shot-item">
          <figcaption>内容页截图 · {{ shotPreview.content.name }}</figcaption>
          <img :src="shotPreview.content.data_url" alt="内容页截图" />
        </figure>
        <figure v-if="shotPreview.author" class="shot-item">
          <figcaption>个人页截图 · {{ shotPreview.author.name }}</figcaption>
          <img :src="shotPreview.author.data_url" alt="个人页截图" />
        </figure>
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

.toolbar-hint {
  font-size: 12px;
}

.toolbar-stats {
  margin-left: auto;
}

.cell-peek {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 4px 10px;
  background: var(--poir-canvas);
  border: 1px solid var(--poir-border);
  border-radius: 6px;
  font-size: 12px;
  min-height: 26px;
}

.peek-label {
  flex: none;
  color: var(--el-text-color-secondary);
}

.peek-value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.peek-tooltip {
  display: inline-block;
  max-width: 420px;
  white-space: pre-wrap;
  word-break: break-all;
}

.grid-wrap {
  height: 74vh;
  min-height: 320px;
  border: 1px solid var(--poir-border);
  border-radius: 6px;
  overflow: hidden;
}

.grid-container {
  width: 100%;
  height: 100%;
}

.shot-pair {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.shot-item {
  flex: 1 1 420px;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.shot-item figcaption {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.shot-item img {
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

/* 单元格悬停提示：跟随鼠标、限宽限长 */
.cell-hover-tip {
  position: fixed;
  z-index: 950;
  max-width: 420px;
  max-height: 180px;
  overflow: hidden;
  padding: 8px 12px;
  background: rgba(32, 33, 36, 0.94);
  color: #fff;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  pointer-events: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

@media (max-width: 640px) {
  .sheet-dialog {
    --el-dialog-width: 100vw !important;
  }
}
</style>

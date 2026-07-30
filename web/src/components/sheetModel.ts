// SheetDialog 的纯数据层：把桥接 payload 转成 Univer 工作簿快照。
// 与 Python 侧 serialize.py 的列定义一一对应。
import { LocaleType } from '@univerjs/presets'
import type { ICellData, IStyleData, IWorkbookData, IWorksheetData } from '@univerjs/presets'

import type { SheetColumn, SheetPayload, SheetRow } from '@/types'

/** 追加在模板列之后的展示辅助列（仅 UI，绝不进交付包）。 */
export const STATUS_COL = '__status'
export const MISSING_COL = '__missing'

export interface DisplayColumn extends SheetColumn {
  width: number
}

export interface SheetModel {
  sheet: SheetPayload
  sheetId: string
  columns: DisplayColumn[]
  /** Univer 行号（从 1 开始，0 是表头） -> payload 行 */
  rowAt: (row: number) => SheetRow | null
}

const HEADER_STYLE: IStyleData = {
  bg: { rgb: '#F2F3F5' },
  bl: 1,
  ht: 1,
  vt: 1,
}

const MISSING_STYLE: IStyleData = { bg: { rgb: '#FDE2E2' } }
const ATTENTION_STYLE: IStyleData = { bg: { rgb: '#FDF0DC' } }
const MANUAL_STYLE: IStyleData = { bg: { rgb: '#E8F3FF' } }

const EXTRA_COLUMNS: DisplayColumn[] = [
  {
    key: STATUS_COL,
    header: '状态',
    field: null,
    editable: false,
    required: false,
    multiline: false,
    choices: [],
    kind: 'text',
    width: 96,
  },
  {
    key: MISSING_COL,
    header: '待补字段',
    field: null,
    editable: false,
    required: false,
    multiline: false,
    choices: [],
    kind: 'text',
    width: 180,
  },
]

function columnWidth(column: SheetColumn): number {
  switch (column.kind) {
    case 'url':
      return 260
    case 'screenshot':
    case 'attachment':
      return 200
    default:
      return column.multiline ? 320 : 140
  }
}

export function displayColumns(sheet: SheetPayload): DisplayColumn[] {
  return [
    ...sheet.columns.map((column) => ({ ...column, width: columnWidth(column) })),
    ...EXTRA_COLUMNS,
  ]
}

function extraCellValue(key: string, row: SheetRow): string {
  if (key === STATUS_COL) return row.status_text
  if (key === MISSING_COL) return row.missing.join('、')
  return ''
}

function cellStyle(column: DisplayColumn, row: SheetRow, value: string): IStyleData | undefined {
  if (column.required && !value.trim()) return MISSING_STYLE
  if (column.key === STATUS_COL && row.attention) return ATTENTION_STYLE
  if (column.key === STATUS_COL && row.manual) return MANUAL_STYLE
  return undefined
}

export function buildCell(row: SheetRow, column: DisplayColumn): ICellData {
  const value = row.cells[column.key] ?? extraCellValue(column.key, row)
  const style = cellStyle(column, row, value)
  return style ? { v: value, s: style } : { v: value }
}

export function buildWorkbookData(payload: SheetPayload[]): {
  workbook: IWorkbookData
  models: SheetModel[]
} {
  const sheets: Record<string, Partial<IWorksheetData>> = {}
  const sheetOrder: string[] = []
  const models: SheetModel[] = []

  payload.forEach((sheet, index) => {
    const sheetId = `sheet_${index}`
    const columns = displayColumns(sheet)
    const cellData: Record<number, Record<number, ICellData>> = { 0: {} }
    columns.forEach((column, columnIndex) => {
      cellData[0][columnIndex] = { v: column.header, s: HEADER_STYLE }
    })
    sheet.rows.forEach((row, rowIndex) => {
      const line: Record<number, ICellData> = {}
      columns.forEach((column, columnIndex) => {
        line[columnIndex] = buildCell(row, column)
      })
      cellData[rowIndex + 1] = line
    })
    const columnData: Record<number, { w: number }> = {}
    columns.forEach((column, columnIndex) => {
      columnData[columnIndex] = { w: column.width }
    })
    sheetOrder.push(sheetId)
    sheets[sheetId] = {
      id: sheetId,
      name: sheet.name,
      cellData,
      columnData,
      rowCount: Math.max(sheet.rows.length + 1, 40),
      columnCount: columns.length,
      defaultColumnWidth: 120,
      freeze: { xSplit: 0, ySplit: 1, startRow: 1, startColumn: 0 },
    }
    models.push({
      sheet,
      sheetId,
      columns,
      rowAt: (row: number) => sheet.rows[row - 1] ?? null,
    })
  })

  return {
    workbook: {
      id: 'poir-workbook',
      name: 'template 预览',
      appVersion: '0.25.1',
      locale: LocaleType.ZH_CN,
      sheetOrder,
      sheets: sheets as IWorkbookData['sheets'],
      styles: {},
    },
    models,
  }
}

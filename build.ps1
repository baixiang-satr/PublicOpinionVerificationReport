# =============================================================================
# 舆情验证报告工具 — 一键编译脚本
# =============================================================================
# 用法（项目根目录下以管理员身份运行 PowerShell）：
#   .\build.ps1                     # 完整构建
#   .\build.ps1 -SkipBrowsers       # 跳过 ms-playwright 浏览器复制
#   .\build.ps1 -SkipOCR            # 跳过 OCR worker 构建
#   .\build.ps1 -SkipFrontend       # 跳过前端构建
#   .\build.ps1 -SkipPyInstaller    # 跳过 PyInstaller 打包（仅刷新资源）
# =============================================================================

param(
    [switch]$SkipBrowsers,
    [switch]$SkipOCR,
    [switch]$SkipFrontend,
    [switch]$SkipPyInstaller,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

$env:PYTHONUTF8 = 1

# 解析 Python 解释器完整路径（py 启动器不继承 PYTHONUTF8，必须用完整路径）
$Python311 = (& py -3.11 -c "import sys; print(sys.executable)").Trim()
if (-not $Python311) { throw "找不到 Python 3.11" }
Write-Host "Python 3.11: $Python311" -ForegroundColor DarkGray

$Python312 = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null).Trim()
$HasPython312 = $true
if (-not $Python312) {
    Write-Host "    [WARN] 未找到 Python 3.12，将跳过 OCR worker 构建" -ForegroundColor Yellow
    $HasPython312 = $false
}

$AppName   = "舆情验证报告工具"
$DistDir   = "$ProjectRoot\dist\$AppName"
$OcrDist   = "$ProjectRoot\output\ocr-dist"

# =============================================================================
# 工具函数
# =============================================================================

function Write-Step([string]$msg) {
    Write-Host "`n>>> $msg" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "    [WARN] $msg" -ForegroundColor Yellow
}

# =============================================================================
# 1. 清理旧构建
# =============================================================================
if ($Clean) {
    Write-Step "清理旧构建产物"
    @("$ProjectRoot\dist", "$ProjectRoot\dist_new", "$ProjectRoot\output",
      "$ProjectRoot\build", "$ProjectRoot\web\dist") | ForEach-Object {
        if (Test-Path $_) {
            Remove-Item $_ -Recurse -Force -ErrorAction SilentlyContinue
            Write-OK "已删除 $_"
        }
    }
    # 清理 PyInstaller 缓存
    $cache = "$env:LOCALAPPDATA\pyinstaller"
    if (Test-Path $cache) { Remove-Item $cache -Recurse -Force -ErrorAction SilentlyContinue }
}

# =============================================================================
# 2. 依赖安装
# =============================================================================
Write-Step "安装 Python 3.11 依赖"
& $Python311 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }
Write-OK "requirements.txt 安装完成"

if (-not $SkipPyInstaller) {
    Write-Step "安装 PyInstaller"
    & $Python311 -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
    Write-OK "PyInstaller 安装完成"
}

# Playwright 浏览器
$PlaywrightBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"
if (-not $SkipBrowsers -and -not (Test-Path $PlaywrightBrowsers)) {
    Write-Step "安装 Playwright Chromium"
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $Python311 -m playwright install chromium 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP
    Write-OK "Playwright Chromium 安装完成"
}

# =============================================================================
# 3. 前端构建
# =============================================================================
if (-not $SkipFrontend) {
    Write-Step "构建前端"
    Set-Location "$ProjectRoot\web"
    if (-not (Test-Path node_modules)) {
        cmd /c "npm install" 2>&1 | Out-Null
        Write-OK "npm install 完成"
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    npm run build 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP
    if (-not (Test-Path "$ProjectRoot\web\dist\index.html")) {
        throw "前端构建失败：web/dist/index.html 未生成"
    }
    Write-OK "Vite build 完成"
    Set-Location $ProjectRoot
}

# =============================================================================
# 4. 生成 template.xlsx
# =============================================================================
Write-Step "生成 template.xlsx"
New-Item -ItemType Directory -Force -Path "$ProjectRoot\template" | Out-Null

$genTemplate = @"
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

wb = openpyxl.Workbook()
wb.remove(wb.active)
thin = Border(left=Side('thin'),right=Side('thin'),top=Side('thin'),bottom=Side('thin'))
hf = Font(bold=True,size=11)
hfill = PatternFill(start_color='D9E1F2',end_color='D9E1F2',fill_type='solid')
ha = Alignment(horizontal='center',vertical='center',wrap_text=True)

sheets = {
    '电商平台': ('商品URL(必填)','发布平台(必填)','商品标题(必填)','处置对象(必填)','处置内容(必填)','店铺名称(必填)','商品截图文件名(必填)','其他附件文件名(多个逗号分隔)'),
    '公众号': ('文章链接(必填)','发布平台(必填)','文章标题(必填)','公众号微信号(必填)','公众号UIN','公众号名称','处置对象(必填)','信息内容(必填)','发布时间(yyyy-mm-dd HH:mm:ss)','文章截图文件名(必填)','其他附件文件名(多个逗号分隔)'),
    '群聊': ('群号','群名称','发布平台(必填)','用户账号','用户id','信息内容(必填)','发布时间(yyyy-mm-dd HH:mm:ss)','群聊截图文件名','其他附件文件名(多个逗号分隔)'),
    '朋友圈': ('用户账号(必填)','用户id','发布平台(必填)','发布时间(yyyy-mm-dd HH:mm:ss)','信息内容(必填)','朋友圈截图文件名','其他附件文件名(多个逗号分隔)'),
    '图文视频': ('URL','用户账号(必填)','昵称(必填)','发布平台(必填)','文本类型(必填)','发布时间(yyyy-mm-dd HH:mm:ss)','信息内容(必填)','账号截图名(必填)','其他文件名(多个逗号分隔)'),
    '微博博客': ('URL(必填)','昵称(必填)','发布平台(必填)','文本类型(必填)','发布时间(yyyy-mm-dd HH:mm:ss)','信息内容(必填)','网页截图名(必填)','其他文件名(多个逗号分隔)'),
    '生活资讯': ('URL(必填)','昵称(必填)','发布平台(必填)','文本类型(必填)','用户账号','发布时间(yyyy-mm-dd HH:mm:ss)','信息内容(必填)','网页截图名(必填)','其他文件名(多个逗号分隔)'),
    '浏览器': ('URL(必填)','用户账号(必填)','昵称(必填)','发布平台(必填)','文本类型(必填)','发布时间(yyyy-mm-dd HH:mm:ss)','信息内容(必填)','账号截图名(必填)','其他文件名(多个逗号分隔)'),
}
for name, headers in sheets.items():
    ws = wb.create_sheet(title=name)
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font, cell.fill, cell.alignment, cell.border = hf, hfill, ha, thin
    for c in range(1, len(headers)+1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = 20
wb.save('template/template.xlsx')
print('template.xlsx created with', len(sheets), 'sheets')
"@

Invoke-Expression "& $Python311 -c `"$genTemplate`"" | Out-Null
Write-OK "template.xlsx 生成完成（8 个工作表）"

# =============================================================================
# 5. OCR Worker 环境
# =============================================================================
if (-not $SkipOCR -and $HasPython312) {
    $OcrVenv = "$ProjectRoot\.ocr-venv"
    if (-not (Test-Path "$OcrVenv\Scripts\python.exe")) {
        & $Python312 -m venv $OcrVenv
        Write-OK "Python 3.12 venv 创建完成"
    }
    & "$OcrVenv\Scripts\python.exe" -m pip install rapidocr-onnxruntime pillow numpy pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "OCR 依赖安装失败" }
    Write-OK "OCR 依赖安装完成"
}

# =============================================================================
# 6. PyInstaller 打包
# =============================================================================
if (-not $SkipPyInstaller) {
    Write-Step "PyInstaller 打包主程序"
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $Python311 -m PyInstaller poir.spec --noconfirm 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP
    # 检查是否成功（dist 目录可能被锁定，使用备用输出）
    if (-not (Test-Path $DistDir)) {
        Write-Warn "dist 目录可能被锁定，使用 dist_new 作为备用输出"
        $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & $Python311 -m PyInstaller poir.spec --noconfirm --distpath dist_new 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        $DistDir = "$ProjectRoot\dist_new\$AppName"
    }
    Write-OK "主程序打包完成"

    if (-not $SkipOCR -and $HasPython312) {
        Write-Step "PyInstaller 打包 OCR worker"
        $workPath = "$ProjectRoot\output\build-ocr-pyi"
        $distPath = "$ProjectRoot\output\ocr-dist"
        $prevEAP = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & "$OcrVenv\Scripts\python.exe" -m PyInstaller poir_ocr_worker.spec --noconfirm --distpath $distPath --workpath $workPath 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        Write-OK "OCR worker 打包完成"
    }
}

# =============================================================================
# 7. 组装发布目录
# =============================================================================
Write-Step "组装发布目录"

# OCR worker
if (-not $SkipOCR -and $HasPython312) {
    $worker = "$OcrDist\poir_ocr_worker.exe"
    if (Test-Path $worker) {
        Copy-Item $worker $DistDir -Force
        Write-OK "poir_ocr_worker.exe 已复制"
    } else {
        Write-Warn "OCR worker 未找到，跳过"
    }
}

# template
Copy-Item "$ProjectRoot\template" "$DistDir\template" -Recurse -Force
Write-OK "template/ 已复制"

# 前端
$webDist = "$ProjectRoot\web\dist"
if (Test-Path $webDist) {
    Copy-Item $webDist "$DistDir\web\dist" -Recurse -Force
    Write-OK "web/dist/ 已复制"
} else {
    Write-Warn "web/dist/ 未找到，请先构建前端"
}

# ms-playwright 浏览器
if (-not $SkipBrowsers) {
    if (Test-Path $PlaywrightBrowsers) {
        Copy-Item $PlaywrightBrowsers "$DistDir\ms-playwright" -Recurse -Force
        Write-OK "ms-playwright/ 已复制"
    } else {
        Write-Warn "ms-playwright 未找到，请先运行: python -m playwright install chromium"
    }
}

# =============================================================================
# 8. 使用说明
# =============================================================================
@"
舆情验证报告工具 — 使用说明
================================

一、运行环境
------------
- Windows 10 / 11（64 位）。
- 无需安装 Python、Node.js 或任何浏览器，已全部随包携带。
- 本工具为 B/S 架构：双击 exe 启动服务后，用浏览器访问
  http://127.0.0.1:16667/（启动时会自动打开浏览器）。

二、启动方法
------------
1. 把本文件夹完整解压到任意位置（不要只解压 exe，整个文件夹都要）。
2. 双击「舆情验证报告工具.exe」，启动较慢（约 5-15 秒）属正常现象。
3. 浏览器自动打开操作页面；也可手动访问 http://127.0.0.1:16667/ 。
   局域网部署：设 POIR_HOST=0.0.0.0 后，其他电脑访问 http://<服务器IP>:16667/ 。

三、文件说明
------------
- output/：每次任务的输出目录（template.zip 在里面），自动创建。
- template/：固定交付模板，请勿修改或删除。
- ms-playwright/：随包浏览器，请勿删除。
- poir_ocr_worker.exe：独立 OCR 识别组件，请勿删除或单独移动。
- 登录态保存在当前 Windows 用户的 AppData 目录（加密存储）。
"@ | Out-File -FilePath "$DistDir\使用说明.txt" -Encoding UTF8
Write-OK "使用说明.txt 已生成"

# =============================================================================
# 9. 完成
# =============================================================================
$totalSize = (Get-ChildItem $DistDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "`n" -NoNewline
Write-Host "========================================" -ForegroundColor Green
Write-Host "  构建完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  输出目录: $DistDir"
Write-Host "  总大小:   $([math]::Round($totalSize, 0)) MB"
Write-Host "  主程序:   $DistDir\舆情验证报告工具.exe"
Write-Host "========================================" -ForegroundColor Green
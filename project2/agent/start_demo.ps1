<#
  报销合规审核 Agent · 录屏启动脚本
  ------------------------------------------------------------------
  放在 project2\agent\ 目录下运行（和 server.py 同级）。

  用法：
    .\start_demo.ps1                      # 默认：清空演示状态 + 启动 + 开浏览器
    .\start_demo.ps1 -KeepState           # 保留台账/队列，直接启动
    .\start_demo.ps1 -Python "E:\conda\python.exe"
    .\start_demo.ps1 -NoBrowser

  作用：
    1. 找到可用的 Python
    2. 检查依赖（缺了会打印安装命令）
    3. 释放被占用的端口
    4. 备份并清空演示台账 / 审计日志 / 待审队列 / 上传图
    5. 启动 server.py，等它就绪
    6. 打开浏览器，打印 4 张示例单和预期结果
#>
[CmdletBinding()]
param(
    [string]$Python,
    [int]$Port = 8010,
    [switch]$KeepState,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here
$base = "http://127.0.0.1:$Port"

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }

# ---------- 1/6 找 Python ----------
Say "`n=== 1/6 查找 Python ==="
$py = $null
if ($Python) {
    if (-not (Test-Path -LiteralPath $Python)) { throw "指定的 Python 不存在: $Python" }
    $py = $Python
} else {
    $cands = @(
        "E:\conda\python.exe",
        (Join-Path $env:USERPROFILE "anaconda3\python.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\python.exe")
    )
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { $py = $c; break } }
    if (-not $py) {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $py = $cmd.Source }
    }
    if (-not $py) { throw "找不到 Python。请用 -Python <解释器路径> 指定。" }
}
Say "  使用: $py"
$ver = & $py -c "import sys; print(sys.version.split()[0])"
Say "  版本: $ver"

# ---------- 2/6 依赖 ----------
Say "`n=== 2/6 检查依赖 ==="
$check = & $py -c "
import importlib.util as u
mods = ['fastapi','uvicorn','requests','langgraph','langgraph.checkpoint.sqlite']
missing = [m for m in mods if u.find_spec(m) is None]
print(','.join(missing))
" 2>&1
if ($LASTEXITCODE -ne 0) {
    Say "  依赖检查失败，Python 输出：" Red
    Write-Host $check
    throw "依赖检查失败"
}
if ("$check".Trim() -ne "") {
    Say "  缺少: $check" Yellow
    Say "  请先执行（在 project2\agent\ 下）:" Yellow
    Say "    & `"$py`" -m pip install -r requirements.txt" White
    throw "依赖不全"
}
Say "  依赖齐全" Green

# ---------- 3/6 端口 ----------
Say "`n=== 3/6 释放端口 $Port ==="
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($c in $conns) {
        Say "  端口被 PID $($c.OwningProcess) 占用，正在结束…" Yellow
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Say "  已释放" Green
} else {
    Say "  端口空闲" Green
}

# ---------- 4/6 清理演示状态 ----------
if (-not $KeepState) {
    Say "`n=== 4/6 清理演示状态 ==="
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $bak = Join-Path $here "_demo_backup_$stamp"
    $files = @("ledger_demo.csv", "audit_log.jsonl", "pending_queue.json")
    $moved = 0
    foreach ($f in $files) {
        $p = Join-Path $here $f
        if (Test-Path -LiteralPath $p) {
            if (-not (Test-Path -LiteralPath $bak)) { New-Item -ItemType Directory -Path $bak -Force | Out-Null }
            Copy-Item -LiteralPath $p -Destination $bak -Force
            Remove-Item -LiteralPath $p -Force
            $moved++
        }
    }
    $up = Join-Path $here "uploads"
    if (Test-Path -LiteralPath $up) {
        $imgs = Get-ChildItem -LiteralPath $up -File -ErrorAction SilentlyContinue
        if ($imgs) {
            if (-not (Test-Path -LiteralPath $bak)) { New-Item -ItemType Directory -Path $bak -Force | Out-Null }
            $upbak = Join-Path $bak "uploads"
            New-Item -ItemType Directory -Path $upbak -Force | Out-Null
            $imgs | Move-Item -Destination $upbak -Force -ErrorAction SilentlyContinue
        }
    }
    Say "  已清 $moved 个状态文件（备份在 _demo_backup_$stamp）" Green
} else {
    Say "`n=== 4/6 跳过清理（-KeepState）==="
}

# ---------- 5/6 启动 ----------
Say "`n=== 5/6 启动服务 ==="
$log = Join-Path $here "demo_server.log"
$err = Join-Path $here "demo_server.err.log"
Remove-Item -LiteralPath $log, $err -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $py `
    -ArgumentList "server.py" `
    -WorkingDirectory $here `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err

$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 800
    if ($proc.HasExited) { break }
    try {
        $r = Invoke-WebRequest -Uri "$base/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if (-not $ready) {
    Say "  启动失败（PID $($proc.Id)）。错误日志尾部：" Red
    if (Test-Path -LiteralPath $err) { Get-Content -LiteralPath $err -Tail 25 | Write-Host }
    Say "`n  常见原因：" Yellow
    Say "   · API key 配置缺失或失效（需要 ZHIPU_* 和 KIMI_* 两组）" Yellow
    Say "   · 数据库文件被别的进程占用" Yellow
    Say "   · 端口仍被占用" Yellow
    throw "服务未就绪"
}

Say "  已就绪: $base  (PID $($proc.Id))" Green
Say "  日志: $log"

# ---------- 6/6 打开浏览器 + 提词卡 ----------
if (-not $NoBrowser) {
    Say "`n=== 6/6 打开浏览器 ==="
    Start-Process "$base/"
}

Say "`n=================== 录屏提词卡 ===================" White
Say " 浏览器窗口请调成 1440x900 以上，Ctrl+0 复位缩放" Gray
Say "" 
Say " 按钮            预期结果            你要讲的点" White
Say " ------------------------------------------------------------" Gray
Say " R01 正常单   ->  pass              不是只会驳回" White
Say " A02 压线违规 ->  reject / 2.1      502 vs 限额 500，规则算出来的" White
Say " D01 超大额   ->  escalate / 1.4    真挂起，批复后续跑入台账" White
Say " E01 伪造     ->  reject / 1.6      查验工具拦截，裸模型看不见" White
Say "==================================================" White
Say ""
Say " 录制顺序建议：R01 -> A02 -> D01（含批复）-> E01" Yellow
Say " 停服：.\stop_demo.ps1" Gray

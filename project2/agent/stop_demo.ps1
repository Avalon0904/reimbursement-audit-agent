<#
  报销合规审核 Agent · 停止脚本
  用法： .\stop_demo.ps1  [-Port 8010]
  作用： 结束占用端口的进程，确认服务已停。
#>
[CmdletBinding()]
param([int]$Port = 8010)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

Write-Host "`n=== 停止演示服务（端口 $Port）===" -ForegroundColor Cyan

$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "  端口 $Port 没有被占用，服务已停" -ForegroundColor Green
} else {
    foreach ($c in $conns) {
        $pid0 = $c.OwningProcess
        $name = (Get-Process -Id $pid0 -ErrorAction SilentlyContinue).ProcessName
        Write-Host "  结束 PID $pid0 ($name)" -ForegroundColor Yellow
        Stop-Process -Id $pid0 -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "  端口仍被占用，请用任务管理器手动结束" -ForegroundColor Red
    } else {
        Write-Host "  已停止" -ForegroundColor Green
    }
}

Write-Host ""

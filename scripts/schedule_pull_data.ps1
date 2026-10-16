<#
.SYNOPSIS
    Daily "Update Local Data" on this PC: registers a Windows Task Scheduler job that runs
    `python -m control_api.tools.pull_data --days 180` from the repo root and appends its output to local_data\pull.log.

.DESCRIPTION
    - Runs daily at -Time (default 07:00 local), and as soon as possible after a missed start (StartWhenAvailable),
      e.g. when the PC was off or asleep at 07:00.
    - Runs as the current user, only while that user is logged on (LogonType Interactive): no password is stored.
    - Read-only toward Atlas: pull_data reads MONGODB_URI / MONGODB_DB from .env.local at the repo root, which should be
      a READ-ONLY Atlas user. This script never reads or prints that file.
    - Writes only under local_data\ (git-ignored).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\schedule_pull_data.ps1 -Register
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\schedule_pull_data.ps1 -Register -Time 06:30 -Python C:\Python311\python.exe
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\schedule_pull_data.ps1 -Status
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\schedule_pull_data.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [switch]$Register,
    [switch]$Unregister,
    [switch]$Status,
    [string]$Python = "",
    [string]$Time = "07:00",
    [int]$Days = 180,
    [string]$TaskName = "KalshiBot-UpdateLocalData"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $RepoRoot "local_data"
$LogFile = Join-Path $DataDir "pull.log"

function Resolve-Python {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit -PathType Leaf)) { throw "Python not found at '$Explicit'." }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    foreach ($name in @("python", "python3", "py")) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $cmd -and $cmd.Source -notlike "*\WindowsApps\*") { return $cmd.Source }
    }
    throw "Python not found on PATH (the Microsoft Store alias is ignored). Pass -Python <full path to python.exe>."
}

function Show-Usage {
    Write-Output "Usage: schedule_pull_data.ps1 -Register [-Time 07:00] [-Python <path>] [-Days 180] | -Unregister | -Status"
}

$chosen = @($Register, $Unregister, $Status) | Where-Object { $_ }
if (@($chosen).Count -ne 1) { Show-Usage; exit 2 }

if ($Register) {
    if ($Days -lt 1) { throw "-Days must be >= 1." }
    $at = [datetime]::ParseExact($Time, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
    $py = Resolve-Python -Explicit $Python
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "control_api\tools\pull_data.py"))) {
        throw "Repo root '$RepoRoot' does not contain control_api\tools\pull_data.py."
    }
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

    # cmd.exe /c "<line>": cmd strips the outer quotes and keeps the inner ones. Output (stdout+stderr) is appended
    # to pull.log with a timestamp header per run.
    $line = "echo ===== %DATE% %TIME% ===== >> `"$LogFile`" && `"$py`" -m control_api.tools.pull_data --days $Days >> `"$LogFile`" 2>&1"
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument "/d /c `"$line`"" -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description "Kalshi maker bot: daily read-only pull of Atlas data into local_data (pull_data --days $Days)." -Force | Out-Null
    Write-Output "Registered '$TaskName': daily at $Time (runs late if missed), as the current user, no stored password."
    Write-Output "  python : $py"
    Write-Output "  repo   : $RepoRoot"
    Write-Output "  log    : $LogFile"
    exit 0
}

if ($Unregister) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { Write-Output "No task named '$TaskName'."; exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Unregistered '$TaskName'. local_data\ was left as is."
    exit 0
}

if ($Status) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { Write-Output "Not registered ('$TaskName')."; exit 1 }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Output "Task       : $TaskName ($($task.State))"
    Write-Output "Last run   : $($info.LastRunTime)  result: $($info.LastTaskResult) (0 = success)"
    Write-Output "Next run   : $($info.NextRunTime)"
    Write-Output "Command    : $($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
    if (Test-Path -LiteralPath $LogFile) {
        Write-Output "Last lines of $LogFile :"
        Get-Content -LiteralPath $LogFile -Tail 15
    } else {
        Write-Output "No log yet ($LogFile)."
    }
    exit 0
}

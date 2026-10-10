# Keep a Windows laptop serving as Stellar's runner (deploy/relay.py) when
# no terminal is open, and bring it back after a restart.
#
#   powershell -ExecutionPolicy Bypass -File deploy\runner_windows.ps1
#
# 1. %UserProfile%\.wslconfig: WSL normally shuts a distribution down 15 s
#    after its last terminal closes, and the VM a minute later; the runner's
#    services would stop with it. instanceIdleTimeout=-1 and vmIdleTimeout=-1
#    keep them running (Microsoft's documented settings).
# 2. A scheduled task that starts the Ubuntu distribution when you sign in;
#    systemd in it then starts Docker and the two Stellar services.
# Nothing here touches the graphics drivers or Docker Desktop.
param([string]$Distro = "Ubuntu")
$ErrorActionPreference = "Stop"

$cfg = Join-Path $env:USERPROFILE ".wslconfig"
$text = if (Test-Path $cfg) { Get-Content $cfg -Raw } else { "" }
function Set-Ini([string]$text, [string]$section, [string]$key, [string]$value) {
    $lines = [System.Collections.Generic.List[string]]($text -split "`r?`n")
    $start = $lines.FindIndex({ param($l) $l.Trim() -ieq "[$section]" })
    if ($start -lt 0) {
        if ($lines.Count -and $lines[$lines.Count - 1].Trim()) { $lines.Add("") }
        $lines.Add("[$section]"); $lines.Add("$key=$value")
        return ($lines -join "`r`n")
    }
    for ($i = $start + 1; $i -lt $lines.Count -and -not $lines[$i].Trim().StartsWith("["); $i++) {
        if ($lines[$i] -match "^\s*$key\s*=") { $lines[$i] = "$key=$value"; return ($lines -join "`r`n") }
    }
    $lines.Insert($start + 1, "$key=$value")
    return ($lines -join "`r`n")
}
if (Test-Path $cfg) { Copy-Item $cfg "$cfg.bak-stellar" -Force }
$text = Set-Ini $text "general" "instanceIdleTimeout" "-1"
$text = Set-Ini $text "wsl2" "vmIdleTimeout" "-1"
Set-Content -Path $cfg -Value $text.TrimEnd() -Encoding ascii
Write-Host "Updated $cfg (backup: .wslconfig.bak-stellar if there was one)"

$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro --exec /bin/true"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "Stellar runner (WSL)" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Starts WSL ($Distro) at sign-in, so Docker and Stellar's runner services start (deploy/relay.py)." -Force | Out-Null
Write-Host "Scheduled task 'Stellar runner (WSL)' starts $Distro when you sign in."
Write-Host "Apply the WSL settings now with: wsl --shutdown   (then: wsl -d $Distro --exec /bin/true)"

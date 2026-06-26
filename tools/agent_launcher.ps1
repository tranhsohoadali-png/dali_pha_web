# DALI Print Agent - nut mot-cham tren Desktop.
# Bat Agent neu CHUA chay (nen, khong cua so) + bao trang thai. Neu DANG chay -> bao da chay.
# Dung -Quiet de chay khong hien popup (cho test).
param([switch]$Quiet)

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root 'dali_print_agent.py'

# Tim pythonw (uu tien Python312 cua user, roi PATH)
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
if (-not (Test-Path $py)) {
  $c = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($c) { $py = $c.Source } else { $py = 'pythonw' }
}

function Get-Agent {
  Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*dali_print_agent*' }
}
function Note([string]$msg, [string]$icon) {
  if ($Quiet) { Write-Output $msg; return }
  Add-Type -AssemblyName System.Windows.Forms | Out-Null
  [System.Windows.Forms.MessageBox]::Show($msg, 'DALI Print Agent', 'OK', $icon) | Out-Null
}

$run = Get-Agent
if ($run) {
  $pid1 = ($run | Select-Object -First 1).ProcessId
  Note "Agent DANG CHAY roi (PID $pid1). File ghep-in se tu sang Flexi." 'Information'
  return
}

Start-Process -FilePath $py -ArgumentList "`"$script`"" -WorkingDirectory $root -WindowStyle Hidden
Start-Sleep -Milliseconds 1200

if (Get-Agent) {
  Note "DA BAT Agent In. Tu gio file ghep-in se tu dong sang Flexi." 'Information'
} else {
  Note "Chua bat duoc Agent. Kiem tra Python hoac tools\dali_agent_config.json." 'Error'
}

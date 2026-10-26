# Start the management app: console server (owns the workflow) + tray, then
# open the console window. Idempotent: reuses whatever is already running.
$proj = '__PROJECT_DIR__'
$py = '__PYTHONW__'
$url = 'http://127.0.0.1:8793/'

$up = $false
try { Invoke-RestMethod -Uri ($url + 'api/app/ping') -TimeoutSec 2 | Out-Null; $up = $true } catch {}
if (-not $up) {
  $cmd = '"' + $py + '" -B -X utf8 -m app.console_server'
  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $proj } | Out-Null
  Start-Sleep -Seconds 3
}

$tray = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'app-tray' }
if (-not $tray) {
  $trayCmd = '"' + $py + '" -B -X utf8 -m app.tray_launcher'
  # WindowStyle alone still permits Windows Terminal to attach a console.
  # CREATE_NO_WINDOW prevents allocating one in the first place.
  $started = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $trayCmd; CurrentDirectory = $proj }
  if ($started.ReturnValue -ne 0) { throw ('Tray startup failed: ' + $started.ReturnValue) }
}

Start-Process msedge.exe -ArgumentList ('--app=' + $url), '--window-size=1280,900'
'app started'

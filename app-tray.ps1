# Tray icon for the management app: close the window to keep running in the
# tray; menu offers "打开管理台" and "关闭程序" (stops workflow + console).
# Also detach when invoked manually by an older shortcut/launcher. This only
# detaches this tray process, never hides a shared terminal window.
Add-Type @"
using System.Runtime.InteropServices;
public static class DouyinTrayConsole {
  [DllImport("kernel32.dll")] public static extern bool FreeConsole();
}
"@
[DouyinTrayConsole]::FreeConsole() | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string name);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}
"@

$proj = '__PROJECT_DIR__'
$iconPath = Join-Path $proj 'app\icon.ico'
$url = 'http://127.0.0.1:8793/'
$title = '抖音私信助手 · 管理台'

$mutex = New-Object System.Threading.Mutex($false, 'DouyinConsoleTray')
if (-not $mutex.WaitOne(0)) { exit 0 }

function Open-Console {
  $h = [Win]::FindWindow($null, $title)
  if ($h -ne [IntPtr]::Zero) { [Win]::ShowWindow($h, 9) | Out-Null; [Win]::SetForegroundWindow($h) | Out-Null }
  else { Start-Process msedge.exe -ArgumentList ('--app=' + $url), '--window-size=1280,900' }
}

$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = New-Object System.Drawing.Icon($iconPath)
$ni.Text = '抖音私信助手'
$ni.Visible = $true
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$open = $menu.Items.Add('打开管理台'); $open.add_Click({ Open-Console })
$astr = $menu.Items.Add('AstrBot 配置页'); $astr.add_Click({
    # Best effort: the console keeps the service alive, but the tray entry must
    # still open the page when the console is older than the AstrBot routes.
    try { Invoke-RestMethod -Uri ($url + 'api/astrbot/start') -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 20 | Out-Null } catch {}
    Start-Process msedge.exe -ArgumentList ('--app=' + 'http://127.0.0.1:6185/'), '--window-size=1180,860'
  })
$quit = $menu.Items.Add('关闭程序'); $quit.add_Click({
    try { Invoke-RestMethod -Uri ($url + 'api/app/quit') -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 5 | Out-Null } catch {}
    $ni.Visible = $false
    [System.Windows.Forms.Application]::Exit()
  })
$ni.ContextMenuStrip = $menu
$ni.add_DoubleClick({ Open-Console })
$ni.add_MouseClick({ param($s, $e) if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) { } })

$global:trayFail = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 4000
$timer.add_Tick({
    try { Invoke-RestMethod -Uri ($url + 'api/app/ping') -TimeoutSec 3 | Out-Null; $global:trayFail = 0 }
    catch { $global:trayFail++; if ($global:trayFail -ge 3) { $ni.Visible = $false; [System.Windows.Forms.Application]::Exit() } }
  })
$timer.Start()
[System.Windows.Forms.Application]::Run()

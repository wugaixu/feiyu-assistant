"""Launch the tray without allocating a Windows console or terminal tab."""
import os
import subprocess
from pathlib import Path


def main():
    from app.live_service import ROOT
    ROOT.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent.parent
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    with (ROOT / 'tray.log').open('ab') as log:
        subprocess.Popen(
            [str(powershell), '-NoProfile', '-NonInteractive', '-WindowStyle',
             'Hidden', '-ExecutionPolicy', 'Bypass', '-File', str(project / 'app-tray.ps1')],
            cwd=project, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


if __name__ == '__main__':
    main()

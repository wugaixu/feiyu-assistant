' GUI-subsystem entry: launch PowerShell hidden, with no cmd.exe window.
Option Explicit
Dim shell, files, folder, powershell, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
command = """" & powershell & """ -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & folder & "\app-start.ps1"""
shell.Run command, 0, False

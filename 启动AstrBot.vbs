Option Explicit
Dim service, result, processId, command
Set service = GetObject("winmgmts:\\.\root\cimv2:Win32_Process")
command = """__PYTHONW__"" ""__PROJECT_DIR__\start_astrbot.py"""
result = service.Create(command, "__PROJECT_DIR__", Null, processId)
If result <> 0 Then MsgBox "AstrBot launcher failed: " & result, vbExclamation

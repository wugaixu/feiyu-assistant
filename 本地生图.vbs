Option Explicit
Dim shell, files, folder, command, pythonw
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
pythonw = "__RUNTIME_ROOT__\tools\local-image\venv\Scripts\pythonw.exe"
shell.CurrentDirectory = folder
command = """" & pythonw & """ -B -X utf8 -m app.manual_images"
shell.Run command, 0, False

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "__PROJECT_DIR__"
shell.Run """__PYTHONW__"" -B -X utf8 -m app.voice_preview", 0, False

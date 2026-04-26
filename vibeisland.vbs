Dim fso, f, pythonPath
Set fso = CreateObject("Scripting.FileSystemObject")
Dim dir : dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set f = fso.OpenTextFile(dir & "\.python-path", 1)
pythonPath = Trim(f.ReadAll())
f.Close
Dim sh : Set sh = CreateObject("WScript.Shell")
sh.Run """" & pythonPath & """ """ & dir & "\src\ui_qml.py""", 0, False

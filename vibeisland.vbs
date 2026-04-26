Dim fso, sh, f, pythonPath
Set fso = CreateObject("Scripting.FileSystemObject")
Dim dir : dir = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(dir & "\.python-path") Then
    MsgBox "Run 'python install.py' first to set up Vibe Island.", 16, "Vibe Island"
    WScript.Quit 1
End If
Set f = fso.OpenTextFile(dir & "\.python-path", 1)
pythonPath = Trim(f.ReadAll())
f.Close
Set sh = CreateObject("WScript.Shell")
sh.Run """" & pythonPath & """ """ & dir & "\src\ui_qml.py""", 0, False

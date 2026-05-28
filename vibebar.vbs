Dim fso, sh, f, pythonPath
Set fso = CreateObject("Scripting.FileSystemObject")
Dim dir : dir = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(dir & "\.python-path") Then
    MsgBox "Run 'python install.py' first to set up VibeBar.", 16, "VibeBar"
    WScript.Quit 1
End If
Set f = fso.OpenTextFile(dir & "\.python-path", 1)
pythonPath = Trim(f.ReadAll())
f.Close

Dim wmi, procs, proc
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='pythonw.exe'")
For Each proc In procs
    If Not IsNull(proc.CommandLine) And InStr(LCase(proc.CommandLine), "ui_qml.py") > 0 Then
        proc.Terminate
    End If
Next

Set sh = CreateObject("WScript.Shell")
sh.Run """" & pythonPath & """ """ & dir & "\src\ui_qml.py""", 0, False

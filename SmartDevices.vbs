' Abre o SmartDevices na bandeja, sem janela de console.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = dir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "Ambiente nao encontrado. Rode 'SmartDevices (terminal).bat' uma vez para prepara-lo.", 48, "SmartDevices"
    WScript.Quit 1
End If
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = dir
shell.Run """" & pythonw & """ """ & dir & "\tray.py""", 0, False

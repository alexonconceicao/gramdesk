# PyInstaller: pyinstaller packaging/gramdesk.spec  (gera dist/Gramdesk/)
import os
import sys

root = os.path.abspath(os.path.join(SPECPATH, ".."))
is_win = sys.platform == "win32"

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[root],
    datas=[
        (os.path.join(root, "gramdesk", "inject.js"), "gramdesk"),
        (os.path.join(root, "gramdesk", "icon.svg"), "gramdesk"),
    ] + (
        # Gerado só no pipeline de release (tools/make_build_info.py); fora do git.
        [(os.path.join(root, "gramdesk", "_build_info.json"), "gramdesk")]
        if os.path.exists(os.path.join(root, "gramdesk", "_build_info.json")) else []
    ),
    hiddenimports=[
        "PyQt6.QtSvg",
        "PyQt6.QtNetwork",
        "PyQt6.QtMultimedia",
        "PyQt6.QtMultimediaWidgets",
    ],
    excludes=["tkinter", "PyQt6.QtQuick3D", "PyQt6.QtBluetooth", "PyQt6.QtDesigner", "PyQt6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Gramdesk",
    console=False,
    icon=os.path.join(SPECPATH, "windows", "gramdesk.ico") if is_win else None,
    version=os.path.join(SPECPATH, "windows", "version.txt") if is_win else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Gramdesk", upx=False)

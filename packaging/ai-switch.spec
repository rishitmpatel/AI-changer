# PyInstaller spec for AI Switch — one-folder build (fewer antivirus false positives, fast start).
#   pyinstaller packaging/ai-switch.spec --noconfirm --clean
# Output: dist/AI Switch/AI Switch.exe (Windows) · dist/AI Switch.app (macOS)
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "ai_switch" / "__init__.py").read_text()).group(1)
ICON_WIN = str(ROOT / "packaging" / "icon.ico")
ICON_MAC = str(ROOT / "packaging" / "icon.icns")

datas = collect_data_files("ai_switch")            # selectors/*.yaml, schema/*.json, web/app/*
datas += collect_data_files("playwright")          # Node driver used to talk CDP (no browsers bundled)
datas += collect_data_files("webview")
hidden = collect_submodules("ai_switch") + collect_submodules("webview.platforms") + ["websocket"]
if sys.platform.startswith("win"):
    hidden += ["clr", "webview.platforms.edgechromium", "webview.platforms.winforms"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

version_file = None
if sys.platform.startswith("win"):
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    nums = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "AI Switch"), StringStruct("FileDescription", "AI Switch"),
            StringStruct("FileVersion", VERSION), StringStruct("ProductName", "AI Switch"),
            StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", "AI Switch.exe"),
            StringStruct("LegalCopyright", "MIT License")])]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])])

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AI Switch",
    console=False,          # windowed app: no terminal pops up
    icon=ICON_WIN if sys.platform.startswith("win") else ICON_MAC,
    version=version_file,
    upx=False,              # UPX-packed exes trigger more antivirus false positives
)
coll = COLLECT(exe, a.binaries, a.datas, name="AI Switch", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AI Switch.app",
        icon=ICON_MAC,
        bundle_identifier="io.github.rishitmpatel.aiswitch",
        version=VERSION,
        info_plist={
            "CFBundleName": "AI Switch",
            "CFBundleDisplayName": "AI Switch",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,   # follow light/dark mode
            "LSMinimumSystemVersion": "11.0",
        },
    )

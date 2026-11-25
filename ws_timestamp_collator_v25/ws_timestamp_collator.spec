# -*- mode: python ; coding: utf-8 -*-
"""
ws_timestamp_collator.spec

Build with:  pyinstaller ws_timestamp_collator.spec

Streamlit + PyInstaller is a known-finicky combination — two specific
failure modes account for almost every report of it online:

1. `importlib.metadata.PackageNotFoundError: streamlit` at runtime.
   Streamlit checks its own installed package metadata on startup;
   PyInstaller strips that by default. Fixed below with copy_metadata().

2. Blank window / 404s on load. Streamlit's frontend is a bundle of
   static HTML/JS/CSS files shipped inside the streamlit package itself;
   PyInstaller's import analysis doesn't know to grab non-Python files.
   Fixed below with collect_all('streamlit'), which pulls in the static
   assets alongside the Python code.

If the build still fails with a "No module named X" or similar at
runtime (not at build time), the fix is almost always adding X to
hiddenimports below and rebuilding — PyInstaller can only see imports it
can find via static analysis, and App.py is bundled as DATA (run as a
script path by Streamlit's own CLI, not imported as a module), so its
imports are invisible to that analysis and must be listed explicitly.
"""

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = []

# Streamlit itself: static frontend assets + everything else it needs
tmp_ret = collect_all("streamlit")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# Package metadata Streamlit and friends check for at runtime via
# importlib.metadata — commonly the actual cause of a build that runs
# fine via `python desktop_launcher.py` but crashes once frozen.
for pkg in [
    "streamlit",
    "pandas",
    "openpyxl",
    "curl_cffi",
    "click",
    "packaging",
    "importlib_metadata",
    "pyarrow",
    "protobuf",
]:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass  # package may not be installed under this exact name — skip rather than fail the build

# Our own app files — bundled as data (not analyzed for imports), since
# they're run by Streamlit as a script path, not imported as a module.
# Anything THEY import that isn't already pulled in above needs adding
# to hiddenimports.
app_files = [
    ("App.py", "."),
    ("scraper.py", "."),
    ("fixture_finder.py", "."),
    ("marker_generator.py", "."),
    ("style.py", "."),
    (".streamlit/config.toml", ".streamlit"),
]
for src, dest in app_files:
    datas.append((src, dest))

hiddenimports += [
    "pandas",
    "openpyxl",
    "curl_cffi",
    "curl_cffi.requests",
    "requests",
    "webview",
]

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WS_Timestamp_Collator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression has a history of flaky interactions with Streamlit's binaries — off by default
    console=False,  # set True temporarily if you need to see startup errors while debugging a failed build
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # point this at a .ico file if you want a custom taskbar/exe icon
)

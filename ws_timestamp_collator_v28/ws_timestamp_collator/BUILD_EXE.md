# Building the desktop .exe

This turns the app into a double-clickable `.exe` that opens its own
window (via pywebview) instead of a browser tab.

**Read this first:** this build step has to run on Windows, once, on
your machine — a PyInstaller build always produces an exe for whatever
OS it's built on, and I can't run Windows here to do it for you. Budget
some patience for the first attempt — Streamlit + PyInstaller is a
known-finicky combination and it's fairly normal to need one or two
rebuild passes to shake out a missing-import error. The spec file
already handles the two most common failure modes (see the comment
block at the top of `ws_timestamp_collator.spec`), but "normal" for this
combination is still not "guaranteed first try."

## 1. Use the same environment the app already runs in

Open the `.bat` launcher once first if you haven't already today, so the
shared venv at `%USERPROFILE%\.ws_tools\ws_timestamp_collator_venv`
exists and has all the normal app dependencies installed. Then:

```
%USERPROFILE%\.ws_tools\ws_timestamp_collator_venv\Scripts\activate.bat
cd path\to\this\folder
pip install -r requirements-desktop.txt
```

## 2. Build

```
pyinstaller ws_timestamp_collator.spec
```

This takes a couple of minutes. Output lands in `dist\WS_Timestamp_Collator\`
(or a single `dist\WS_Timestamp_Collator.exe` if you switch the spec to
one-file mode later — the spec as given produces a folder, which starts
faster since nothing needs unpacking on every launch).

## 3. If it fails to build

Check the actual error — PyInstaller is usually specific about what's
missing. Common fixes:
- `ModuleNotFoundError` for something at **runtime** (app opens, then
  crashes) → add that package name to `hiddenimports` in the .spec file
  and rebuild.
- `PackageNotFoundError: <package>` → add that package name to the
  `copy_metadata` loop near the top of the .spec file and rebuild.
- Blank window, nothing loads → almost always the `collect_all("streamlit")`
  step not finding Streamlit's static frontend files; confirm you're
  building from inside the venv where Streamlit is actually installed
  (step 1), not your system Python.

## 4. If it builds but won't start

Temporarily flip `console=False` to `console=True` in the .spec file and
rebuild — that gives you a visible console window with the real
traceback instead of a silent failure, which is the fastest way to see
what's actually going wrong. Flip it back once it's working.

## 5. Run it

`dist\WS_Timestamp_Collator\WS_Timestamp_Collator.exe` — double-click it
like any other program. First launch may take a few seconds longer than
later ones while Windows Defender does its usual first-run scan of a new
unsigned exe (this app isn't code-signed, so Defender/SmartScreen may
also show an "unknown publisher" warning the very first time — that's
expected for anything built like this without a paid code-signing
certificate, not a sign anything's wrong).

## Distributing it

The whole `dist\WS_Timestamp_Collator\` folder is what you'd zip up and
share if you ever do release this publicly — the `.exe` alone won't work
without the rest of that folder sitting next to it.

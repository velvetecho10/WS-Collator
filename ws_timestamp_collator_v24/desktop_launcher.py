"""
desktop_launcher.py
Entry point for the desktop-app build. Starts the Streamlit server on a
free local port in a background thread, waits for it to come up, then
opens a native pywebview window pointed at it — so the end result feels
like a real desktop app (its own window, its own taskbar entry) rather
than "a website that launches itself in your browser."

Streamlit itself is still, under the hood, a local web server — pywebview
just gives you an embedded Chromium view instead of handing that URL to
your system browser. There's no way around that with Streamlit's
architecture; this is the standard pattern for wrapping a Streamlit app
as a desktop app (used by several open-source "streamlit-desktop"
projects), not something unusual to this app specifically.
"""

import os
import sys
import socket
import threading
import time
import urllib.request
import urllib.error


def _app_dir() -> str:
    """
    Path to the app's own files. When frozen by PyInstaller, everything
    bundled as `datas` is extracted to a temp folder at sys._MEIPASS at
    runtime — when running unfrozen (plain `python desktop_launcher.py`
    during development), it's just the folder this file lives in.
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_streamlit(script_path: str, port: int) -> None:
    """
    Runs Streamlit's own CLI entry point in-process, pointed at App.py, on
    a fixed local port, headless (no browser auto-open — pywebview is our
    "browser" here), with the file watcher off (irrelevant once frozen,
    and avoids extra background threads in a packaged exe).
    """
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
        "--global.developmentMode",
        "false",
    ]
    stcli.main()


def _wait_for_server(url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def main() -> None:
    app_dir = _app_dir()
    script_path = os.path.join(app_dir, "App.py")

    if not os.path.exists(script_path):
        # Fail loudly with something visible rather than a silent exit,
        # since a packaged exe has no console attached by default.
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "WS Timestamp Collator",
            f"Couldn't find App.py at:\n{script_path}\n\n"
            "This usually means the PyInstaller build is missing the app "
            "files as bundled data — check the .spec file's `datas` list.",
        )
        sys.exit(1)

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    server_thread = threading.Thread(target=_run_streamlit, args=(script_path, port), daemon=True)
    server_thread.start()

    if not _wait_for_server(url):
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "WS Timestamp Collator",
            "The app server didn't start in time. Try closing and "
            "reopening — if this keeps happening, run the .bat launcher "
            "instead to see the actual error in a console window.",
        )
        sys.exit(1)

    import webview

    webview.create_window(
        "WS Timestamp Collator",
        url,
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()

"""A real, persistent Chromium the user logs into ONCE; shortforge drives it over CDP.

Why not Playwright's own launch(): Google's sign-in refuses browsers that advertise
automation ("This browser or app may not be secure"). A normal Chromium started with
only --remote-debugging-port and a private user-data-dir does not advertise it; the
user signs in by hand like any browser, cookies persist in the profile, and later
runs attach with connect_over_cdp without touching the login.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

CANDIDATES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "brave", "microsoft-edge"]


def find_browser() -> str | None:
    for name in CANDIDATES:
        p = shutil.which(name)
        if p:
            return p
    # Playwright's bundled chromium as a last resort (works for uploads; Google login may refuse it)
    for base in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), Path.home() / ".cache" / "ms-playwright"):
        if not base:
            continue
        base = Path(base)
        if base.exists():
            for p in sorted(base.glob("chromium*/chrome-linux/chrome")):
                return str(p)
            for p in sorted(base.glob("chromium*/chrome-linux/headless_shell")):
                return str(p)
    return None


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as r:
            return "webSocketDebuggerUrl" in json.loads(r.read().decode())
    except Exception:
        return False


class Browser:
    def __init__(self, profile_dir: Path, port: int = 9333, log=print, headless: bool = False, exe: str | None = None):
        self.profile_dir = Path(profile_dir)
        self.port = port
        self.log = log
        self.headless = headless
        self.exe = exe or find_browser()
        self.proc: subprocess.Popen | None = None
        self._pw = None
        self._browser = None

    def ensure_running(self) -> None:
        if cdp_alive(self.port):
            return
        if not self.exe:
            raise RuntimeError("no Chromium/Chrome found — install `chromium` (pacman -S chromium / apt install chromium)")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            self.exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--window-size=1280,900",
        ]
        if self.headless:
            args += ["--headless=new", "--disable-gpu"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")  # Chromium refuses to start as root otherwise (containers, CI)
        self.log(f"[browser] starting {self.exe} (profile {self.profile_dir}, cdp {self.port})")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(60):
            if cdp_alive(self.port):
                return
            time.sleep(0.5)
        raise RuntimeError("browser did not open its debugging port in 30s")

    def connect(self):
        """Returns a Playwright BrowserContext attached to the running Chromium (default profile context)."""
        from playwright.sync_api import sync_playwright

        self.ensure_running()
        if self._pw is None:
            self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        return ctx

    def open_url(self, url: str):
        ctx = self.connect()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        return page

    def close_connection(self) -> None:
        try:
            if self._browser:
                self._browser.close()  # detaches only; the Chromium process keeps running with the profile
        except Exception:
            pass
        self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def stop(self) -> None:
        self.close_connection()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

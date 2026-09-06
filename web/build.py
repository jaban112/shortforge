"""Build the single-file setup page: inline libsodium + a base64 manifest of the repo.

    python web/build.py            -> shortforge-setup.html (repo root)
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".git", "models", "out", "work", "__pycache__", ".pytest_cache", ".build", "node_modules"}
EXCLUDE_FILES = {"shortforge-setup.html", ".env", "ledger.sqlite", "ig_token.enc", "sodium.bundle.js"}
EXCLUDE_SUFFIX = {".egg-info", ".pyc", ".mp4", ".wav", ".onnx", ".bin"}


def manifest() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if p.name in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIX:
            continue
        out[rel.as_posix()] = base64.b64encode(p.read_bytes()).decode("ascii")
    return out


def version() -> str:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0"


def sodium_bundle() -> str:
    """esbuild libsodium-wrappers into an IIFE exposing window.sodiumReady (cached in web/sodium.bundle.js)."""
    cached = ROOT / "web" / "sodium.bundle.js"
    if cached.exists() and cached.stat().st_size > 500_000:
        return cached.read_text(encoding="utf-8")
    work = ROOT / "web" / ".build"
    work.mkdir(exist_ok=True)
    (work / "entry.js").write_text("import _s from 'libsodium-wrappers';\nwindow.sodiumReady = _s.ready.then(() => _s);\n", encoding="utf-8")
    if not (work / "node_modules").exists():
        subprocess.run(["npm", "init", "-y"], cwd=work, check=True, capture_output=True)
        subprocess.run(["npm", "i", "libsodium-wrappers@0.7.15", "esbuild@0.24.2", "--silent"], cwd=work, check=True)
    subprocess.run(["npx", "esbuild", "entry.js", "--bundle", "--format=iife", "--minify", f"--outfile={cached}"], cwd=work, check=True, capture_output=True)
    return cached.read_text(encoding="utf-8")


def build(out: Path = ROOT / "shortforge-setup.html") -> Path:
    tpl = (ROOT / "web" / "app.html").read_text(encoding="utf-8")
    m = manifest()
    js = sodium_bundle()
    if "</script>" in js:
        raise RuntimeError("sodium bundle contains </script>")
    html = tpl.replace("/*__SODIUM__*/", js, 1)
    html = html.replace("/*__MANIFEST__*/{}", json.dumps(m, separators=(",", ":")), 1)
    html = html.replace("/*__VERSION__*/", version(), 1)
    out.write_text(html, encoding="utf-8")
    print(f"{out} — {len(m)} files embedded, {out.stat().st_size / 1024:.0f} KB")
    return out


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shortforge-setup.html")

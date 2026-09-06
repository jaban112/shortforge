#!/usr/bin/env bash
# shortforge one-shot installer (Arch / Debian / Ubuntu, incl. ChromeOS Linux).
#   bash install.sh          → installs ffmpeg + chromium + python deps into ./.venv,
#                              a `shortforge` launcher, a background service, and opens the app.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

say() { printf '\033[1;32m▸ %s\033[0m\n' "$*"; }

say "system packages"
if command -v pacman >/dev/null; then
  sudo pacman -Sy --needed --noconfirm ffmpeg chromium python python-pip ttf-dejavu
elif command -v apt-get >/dev/null; then
  sudo apt-get update -q
  sudo apt-get install -y -q ffmpeg chromium python3 python3-venv python3-pip fonts-dejavu-core || sudo apt-get install -y -q chromium-browser
else
  echo "unknown package manager — install ffmpeg, chromium, python3 yourself, then rerun"; exit 1
fi

say "python environment (.venv)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

say "launcher: ~/.local/bin/shortforge"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/shortforge" <<EOF
#!/usr/bin/env bash
cd "$HERE" && exec .venv/bin/python -m shortforge "\$@"
EOF
chmod +x "$HOME/.local/bin/shortforge"

say "background service (starts with the Linux VM, restarts if it dies)"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/shortforge-web.service" <<EOF
[Unit]
Description=shortforge local app (http://127.0.0.1:8787)
After=network-online.target

[Service]
WorkingDirectory=$HERE
ExecStart=$HERE/.venv/bin/python -m shortforge web --no-open
Restart=always
RestartSec=5
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now shortforge-web.service || true
loginctl enable-linger "$USER" 2>/dev/null || true

say "app shortcut"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/shortforge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=shortforge
Comment=Faceless Shorts factory
Exec=xdg-open http://127.0.0.1:8787
Icon=video-display
Terminal=false
Categories=AudioVideo;
EOF

say "TTS model (350 MB, once)"
.venv/bin/python -c "from shortforge.tts import ensure_models; from pathlib import Path; ensure_models(Path('models'))"

sleep 2
say "done → http://127.0.0.1:8787  (also in the ChromeOS launcher as 'shortforge')"
xdg-open http://127.0.0.1:8787 >/dev/null 2>&1 || true

#!/usr/bin/env bash
# NetScope - post-install setup. Developed for and by frig.
#
#   ./install.sh              link the CLI, install the launcher, check deps
#   ./install.sh --enable     also add the bar widget to the right section
#   ./install.sh --watch      also enable the background watcher (systemd --user)
#
# Installing the plugin itself is `omarchy plugin add <git-url>`; this script
# links the `netscope` command and the desktop launcher, which the window and
# app grid use. The bar widget is driven by the manifest.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CLI="$HERE/bin/netscope"

# --- dependencies ---------------------------------------------------------- #
missing=()
for dep in python3 ip ping curl; do
  command -v "$dep" >/dev/null || missing+=("$dep")
done
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); from gi.repository import Gtk, Adw' 2>/dev/null \
  || missing+=("python-gobject + gtk4 + libadwaita")
if ((${#missing[@]})); then
  echo "install.sh: missing dependencies: ${missing[*]}" >&2
  echo "  install with: omarchy pkg add python-gobject gtk4 libadwaita" >&2
  exit 1
fi
command -v avahi-resolve >/dev/null || echo "note: avahi not found - mDNS names will be skipped (optional)"
command -v claude >/dev/null || command -v gemini >/dev/null || command -v codex >/dev/null \
  || echo "note: no AI CLI found (claude/gemini/codex) - the AI SCAN button stays disabled (optional)"

# --- link the CLI ---------------------------------------------------------- #
mkdir -p "$HOME/.local/bin"
ln -sf "$CLI" "$HOME/.local/bin/netscope"
echo "==> linked ~/.local/bin/netscope"

# --- desktop launcher ------------------------------------------------------ #
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
install -m644 "$HERE/netscope.desktop" "$APPS/netscope.desktop"
# icon for the app grid
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICONS"
install -m644 "$HERE/icon.png" "$ICONS/netscope.png"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "==> installed launcher + icon"

# --- optional: bar widget -------------------------------------------------- #
enable=0; watch=0
for a in "${@:-}"; do
  [ "$a" = "--enable" ] && enable=1
  [ "$a" = "--watch" ] && watch=1
done
if [ "$enable" = 1 ]; then
  omarchy plugin enable io.github.frigstah.netscope right 2>/dev/null \
    || omarchy bar put io.github.frigstah.netscope --section right 2>/dev/null \
    || echo "note: could not auto-place the widget; add it with: omarchy bar put io.github.frigstah.netscope --section right"
  echo "==> added the NetScope bar widget"
fi

# --- optional: background watcher (systemd --user) ------------------------- #
if [ "$watch" = 1 ]; then
  UNITDIR="$HOME/.config/systemd/user"
  mkdir -p "$UNITDIR"
  install -m644 "$HERE/systemd/netscope-watch.service" "$UNITDIR/netscope-watch.service"
  if command -v systemctl >/dev/null; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now netscope-watch.service 2>/dev/null \
      && echo "==> watcher enabled (systemctl --user status netscope-watch)" \
      || echo "note: could not start the watcher service; start it with: systemctl --user enable --now netscope-watch.service"
  else
    echo "note: systemd --user not available; run the watcher manually with: netscope --watch"
  fi
fi

echo
echo "Done. Launch it with:  netscope"
echo "Add the bar widget with:  omarchy plugin enable io.github.frigstah.netscope right"

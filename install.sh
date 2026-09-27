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
if ((${#missing[@]})); then
  echo "install.sh: missing dependencies: ${missing[*]}" >&2
  exit 1
fi
# Only the window needs the toolkit, so this is a note and not a failure: the
# command line, `netscope --tui` and the watcher install and run without it.
if ! python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); from gi.repository import Gtk, Adw; raise SystemExit(0 if (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12) else 1)' 2>/dev/null; then
  echo "note: python-gobject, gtk4 4.12+ and libadwaita not available - the NetScope window needs them"
  echo "  install with: omarchy pkg add python-gobject gtk4 libadwaita"
  echo "  the command line, 'netscope --tui' and the watcher work without them"
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || echo "note: python3 is older than 3.11 - the window needs 3.11+; the command line and 'netscope --tui' do not"
command -v avahi-resolve >/dev/null || echo "note: avahi not found - mDNS names will be skipped (optional)"
command -v claude >/dev/null || command -v codex >/dev/null \
  || curl -sf -m 2 -o /dev/null "${NETSCOPE_OLLAMA_HOST:-http://localhost:11434}/api/tags" \
  || echo "note: no AI engine found - AI SCAN needs claude or codex, or a local 'ollama serve' (optional)"
if command -v claude >/dev/null || command -v codex >/dev/null; then
  command -v bwrap >/dev/null \
    || echo "note: bubblewrap not found - a cloud AI CLI is only ever run inside it, so cloud engines will not be offered (pacman -S bubblewrap). A local Ollama model needs no sandbox."
fi

# --- files at shared paths ------------------------------------------------- #
# ~/.local/bin, the launcher and icon directories and the systemd user
# directory are shared with everything else you have installed. A target is
# written only when nothing is there yet, when it already holds exactly what
# NetScope installs, or when NetScope put it there itself and nothing has
# changed it since - the list of what it placed is kept in $RECORD. Anything
# else is left alone and reported: replacing a file of your own is yours to
# do, by removing it and running this again.
RECORD="${XDG_STATE_HOME:-$HOME/.local/state}/netscope/installed"
mkdir -p "$(dirname "$RECORD")"
touch "$RECORD"
skipped=()

recorded() {    # what the record says NetScope left at a path, if anything
  awk -F'\t' -v p="$1" '$2 == p { v = $1 } END { if (v != "") print v }' "$RECORD"
}

remember() {    # path, what is there now
  local tmp
  tmp=$(mktemp "$RECORD.XXXXXX")
  awk -F'\t' -v p="$1" '$2 != p' "$RECORD" >"$tmp"
  printf '%s\t%s\n' "$2" "$1" >>"$tmp"
  mv -f "$tmp" "$RECORD"
}

place_file() {  # source, target
  local want have
  want=$(sha256sum <"$1" | cut -d' ' -f1)
  if [ -L "$2" ] || { [ -e "$2" ] && [ ! -f "$2" ]; }; then
    skipped+=("$2")               # a link or a directory NetScope never makes
    return 1
  fi
  if [ -f "$2" ]; then
    have=$(sha256sum <"$2" | cut -d' ' -f1)
    if [ "$have" != "$want" ] && [ "$have" != "$(recorded "$2")" ]; then
      skipped+=("$2")             # someone else's file, or ours edited since
      return 1
    fi
  fi
  mkdir -p "$(dirname "$2")"
  install -m644 "$1" "$2" || return 1
  remember "$2" "$want"
}

place_link() {  # what it points to, the link
  local now
  if [ -L "$2" ]; then
    now=$(readlink "$2")
    if [ "$now" = "$1" ]; then
      remember "$2" "link:$1"
      return 0
    fi
    if [ "link:$now" != "$(recorded "$2")" ]; then
      skipped+=("$2")
      return 1
    fi
    rm -f "$2"                    # NetScope's own link to where it used to live
  elif [ -e "$2" ]; then
    skipped+=("$2")
    return 1
  fi
  mkdir -p "$(dirname "$2")"
  # plain ln -s refuses to replace anything that appeared in the meantime
  ln -s "$1" "$2" 2>/dev/null || { skipped+=("$2"); return 1; }
  remember "$2" "link:$1"
}

# --- link the CLI ---------------------------------------------------------- #
place_link "$CLI" "$HOME/.local/bin/netscope" && echo "==> linked ~/.local/bin/netscope"

# --- desktop launcher ------------------------------------------------------ #
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"
if place_file "$HERE/netscope.desktop" "$APPS/netscope.desktop"; then
  echo "==> installed the launcher"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
fi
if place_file "$HERE/icon.png" "$ICONS/netscope.png"; then
  echo "==> installed the icon"
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# --- optional: bar widget -------------------------------------------------- #
enable=0; watch=0
for a in "${@:-}"; do
  [ "$a" = "--enable" ] && enable=1
  [ "$a" = "--watch" ] && watch=1
done
if [ "$enable" = 1 ]; then
  if omarchy plugin enable io.github.frigstah.netscope right 2>/dev/null \
    || omarchy bar put io.github.frigstah.netscope --section right 2>/dev/null; then
    echo "==> added the NetScope bar widget"
  else
    echo "note: could not auto-place the widget; add it with: omarchy bar put io.github.frigstah.netscope --section right"
  fi
fi

# --- optional: background watcher (systemd --user) ------------------------- #
if [ "$watch" = 1 ]; then
  UNITDIR="$HOME/.config/systemd/user"
  if ! place_file "$HERE/systemd/netscope-watch.service" "$UNITDIR/netscope-watch.service"; then
    echo "note: the watcher was not enabled - a netscope-watch.service that NetScope did not install is already there"
  elif command -v systemctl >/dev/null; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now netscope-watch.service 2>/dev/null \
      && echo "==> watcher enabled (systemctl --user status netscope-watch)" \
      || echo "note: could not start the watcher service; start it with: systemctl --user enable --now netscope-watch.service"
  else
    echo "note: systemd --user not available; run the watcher manually with: netscope --watch"
  fi
fi

if ((${#skipped[@]})); then
  echo
  echo "note: left these alone - something NetScope did not install is already there:"
  printf '  %s\n' "${skipped[@]}"
  echo "  to install NetScope's own there, remove it and run install.sh again"
fi

echo
if [ "$(recorded "$HOME/.local/bin/netscope")" = "link:$CLI" ]; then
  echo "Done. Launch it with:  netscope"
else
  echo "Done. Launch it with:  $CLI"
fi
echo "Add the bar widget with:  omarchy plugin enable io.github.frigstah.netscope right"

"""Omarchy theme bridge: colors + font for the NetScope GUI."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

THEME_DIR = Path.home() / ".local/state/omarchy/current/theme"
COLORS_FILE = THEME_DIR / "colors.toml"

FALLBACK = {
    "mode": "dark",
    "accent": "#7fd7ff",
    "selection": "#1a1a1a",
    "muted": "#7a7a7a",
    "background": "#000000",
    "dark_background": "#090909",
    "darker_background": "#070707",
    "lighter_background": "#1a1a1a",
    "foreground": "#e6e6e6",
    "dark_foreground": "#505050",
    "light_foreground": "#ececec",
    "bright_foreground": "#ffffff",
    "red": "#ff5f5f",
    "yellow": "#ffd75f",
    "orange": "#ffaf5f",
    "green": "#5fff87",
    "cyan": "#5fd7ff",
    "blue": "#5f87ff",
    "magenta": "#d75fff",
    "brown": "#8a6a4a",
}


def colors() -> dict[str, str]:
    c = dict(FALLBACK)
    try:
        data = tomllib.loads(COLORS_FILE.read_text())
        for k, v in data.items():
            if isinstance(v, str):
                c[k] = v
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return c


def font_family() -> str:
    try:
        out = subprocess.run(
            ["omarchy-font-current"], capture_output=True, text=True, timeout=3, check=False
        ).stdout.strip()
        if out and "unknown" not in out.lower():
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "JetBrainsMono Nerd Font"


def font_size() -> int:
    try:
        data = tomllib.loads((Path.home() / ".config/omarchy/shell.toml").read_text())
        return int(data.get("font", {}).get("base-size", 12))
    except (OSError, tomllib.TOMLDecodeError, ValueError, AttributeError):
        return 12


def theme_name() -> str:
    try:
        return (Path.home() / ".local/state/omarchy/current/theme.name").read_text().strip()
    except OSError:
        return "unknown"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return 255, 255, 255


def rgba(h: str, a: float) -> str:
    r, g, b = _hex_to_rgb(h)
    return f"rgba({r},{g},{b},{a:.3f})"


def is_monochrome(c: dict[str, str]) -> bool:
    """Themes like Vantablack are grey-only; accent then needs a little help."""
    r, g, b = _hex_to_rgb(c["accent"])
    return max(r, g, b) - min(r, g, b) < 12


def css(c: dict[str, str] | None = None) -> str:
    c = c or colors()
    fam = font_family()
    size = font_size()
    accent = c["accent"]
    fg = c["foreground"]
    bg = c["background"]
    bg2 = c["lighter_background"]
    bg1 = c["dark_background"]
    muted = c["muted"]
    good = c["green"]
    warn = c["yellow"]
    bad = c["red"]
    mono = is_monochrome(c)
    # on monochrome themes, the "signal" colour is the bright foreground so the
    # glow still reads; on colourful themes the accent carries it
    glow = c["bright_foreground"] if mono else accent
    return f"""
    * {{
      font-family: "{fam}", monospace;
      font-size: {size}pt;
      outline-width: 0;
      -gtk-icon-style: symbolic;
    }}
    window, .ns-root {{
      background-color: {bg};
      color: {fg};
    }}
    .ns-header {{
      background-color: {bg1};
      border-bottom: 1px solid {rgba(glow, 0.35)};
      padding: 8px 14px;
    }}
    .ns-brand {{
      color: {glow};
      font-weight: 800;
      font-size: {size + 4}pt;
      letter-spacing: 4px;
    }}
    .ns-brand-sub {{
      color: {muted};
      letter-spacing: 2px;
      font-size: {size - 1}pt;
    }}
    .ns-live {{
      color: {good};
      font-weight: 700;
      letter-spacing: 1px;
    }}
    .ns-live.dim {{ color: {rgba(good, 0.35)}; }}
    .ns-busy {{ color: {warn}; }}
    .ns-sidebar {{
      background-color: {bg1};
      border-right: 1px solid {rgba(glow, 0.25)};
    }}
    .ns-section {{
      color: {rgba(glow, 0.85)};
      font-weight: 700;
      letter-spacing: 3px;
      font-size: {size - 2}pt;
      padding: 10px 12px 4px 12px;
    }}
    .ns-card {{
      background-color: {rgba(fg, 0.03)};
      border: 1px solid {rgba(glow, 0.22)};
      border-left: 3px solid {rgba(glow, 0.55)};
      padding: 8px 10px;
      margin: 3px 10px;
    }}
    .ns-card.down {{
      border-left-color: {rgba(muted, 0.5)};
      opacity: 0.65;
    }}
    .ns-card.primary {{
      border-left-color: {glow};
      background-color: {rgba(glow, 0.07)};
    }}
    .ns-card-title {{
      color: {c['bright_foreground']};
      font-weight: 700;
      letter-spacing: 1px;
    }}
    .ns-card-kind {{
      color: {muted};
      font-size: {size - 2}pt;
      letter-spacing: 1px;
    }}
    .ns-ip {{
      color: {glow};
      font-weight: 700;
      font-size: {size + 1}pt;
    }}
    .ns-big-ip {{
      color: {glow};
      font-weight: 800;
      font-size: {size + 6}pt;
      letter-spacing: 1px;
    }}
    .ns-dim {{ color: {muted}; }}
    .ns-dimmer {{ color: {c['dark_foreground']}; }}
    .ns-good {{ color: {good}; }}
    .ns-warn {{ color: {warn}; }}
    .ns-bad {{ color: {bad}; }}
    .ns-accent {{ color: {glow}; }}
    .ns-mono-small {{ font-size: {size - 1}pt; }}

    .ns-toolbar {{
      padding: 8px 12px;
      border-bottom: 1px solid {rgba(glow, 0.18)};
      background-color: {rgba(fg, 0.02)};
    }}
    .ns-toolbar-title {{
      color: {glow};
      font-weight: 700;
      letter-spacing: 3px;
    }}

    button, .ns-btn {{
      background-color: transparent;
      background-image: none;
      color: {glow};
      border: 1px solid {rgba(glow, 0.55)};
      border-radius: 0;
      padding: 4px 12px;
      font-weight: 700;
      letter-spacing: 2px;
      box-shadow: none;
      text-shadow: none;
      min-height: 24px;
    }}
    button:hover {{
      background-color: {rgba(glow, 0.12)};
      border-color: {glow};
    }}
    button:active, button.ns-armed {{
      background-color: {rgba(glow, 0.25)};
    }}
    button:disabled {{
      color: {rgba(muted, 0.6)};
      border-color: {rgba(muted, 0.3)};
    }}
    button.ns-danger {{
      color: {bad};
      border-color: {rgba(bad, 0.6)};
    }}
    button.ns-danger:hover {{ background-color: {rgba(bad, 0.15)}; border-color: {bad}; }}
    button.ns-ghost {{
      border-color: {rgba(glow, 0.25)};
      color: {rgba(fg, 0.8)};
      font-weight: 500;
      letter-spacing: 1px;
    }}
    button.ns-ai {{
      color: {bg};
      background-color: {glow};
      border-color: {glow};
      font-weight: 800;
    }}
    button.ns-ai:hover {{
      background-color: {c['bright_foreground']};
      border-color: {c['bright_foreground']};
    }}
    button.ns-ai:disabled {{
      color: {rgba(muted, 0.7)};
      background-color: transparent;
      border-color: {rgba(muted, 0.3)};
    }}
    .ns-report, .ns-report text {{
      background-color: {c['darker_background']};
      color: {rgba(fg, 0.92)};
    }}
    .ns-bad-live {{ color: {bad}; }}

    .ns-sec-strip {{
      padding: 5px 12px;
      border-bottom: 1px solid {rgba(glow, 0.15)};
      background-color: {rgba(fg, 0.02)};
    }}
    .ns-risk-badge {{
      font-weight: 800;
      letter-spacing: 1px;
      font-size: {size - 2}pt;
      padding: 1px 8px;
      color: {bg};
    }}
    .ns-risk-badge.clean {{ background-color: {good}; }}
    .ns-risk-badge.low {{ background-color: {rgba(good, 0.8)}; }}
    .ns-risk-badge.elevated {{ background-color: {warn}; }}
    .ns-risk-badge.high {{ background-color: {bad}; }}
    .ns-sev {{
      font-weight: 800;
      font-size: {size - 3}pt;
      letter-spacing: 1px;
    }}
    .ns-sev.high {{ color: {bad}; }}
    .ns-sev.medium {{ color: {warn}; }}
    .ns-sev.low {{ color: {rgba(good, 0.85)}; }}
    .ns-sev.info {{ color: {muted}; }}
    .ns-findings-pop, .ns-findings-pop > contents {{
      background-color: {bg1};
      border: 1px solid {rgba(glow, 0.5)};
      border-radius: 0;
    }}

    dropdown > button, dropdown button.toggle {{
      color: {fg};
      font-weight: 500;
      letter-spacing: 0;
      padding: 3px 8px;
      border: 1px solid {rgba(glow, 0.4)};
    }}
    dropdown arrow {{ color: {glow}; }}
    popover, popover > contents, popover.menu {{
      background-color: {bg1};
      color: {fg};
      border: 1px solid {rgba(glow, 0.5)};
      border-radius: 0;
      box-shadow: none;
    }}
    popover listview > row, popover modelbutton {{
      padding: 4px 10px;
      color: {fg};
    }}
    popover listview > row:hover, popover listview > row:selected {{
      background-color: {rgba(glow, 0.18)};
      color: {c['bright_foreground']};
    }}

    entry {{
      background-color: {rgba(fg, 0.04)};
      background-image: none;
      color: {fg};
      border: 1px solid {rgba(glow, 0.4)};
      border-radius: 0;
      caret-color: {glow};
      padding: 2px 8px;
      min-height: 24px;
      box-shadow: none;
    }}
    entry:focus {{ border-color: {glow}; }}
    entry selection {{ background-color: {rgba(glow, 0.35)}; }}

    progressbar {{ min-height: 3px; }}
    progressbar trough {{
      background-color: {rgba(fg, 0.06)};
      border: none;
      border-radius: 0;
      min-height: 3px;
    }}
    progressbar progress {{
      background-color: {glow};
      background-image: none;
      border: none;
      border-radius: 0;
      min-height: 3px;
    }}

    columnview, listview {{
      background-color: transparent;
      color: {fg};
    }}
    columnview > header {{
      background-color: {rgba(fg, 0.03)};
      border-bottom: 1px solid {rgba(glow, 0.3)};
    }}
    columnview > header > button {{
      background: none;
      border: none;
      border-right: 1px solid {rgba(glow, 0.12)};
      border-radius: 0;
      color: {rgba(glow, 0.9)};
      padding: 4px 8px;
      font-size: {size - 2}pt;
      letter-spacing: 2px;
      font-weight: 700;
    }}
    columnview > header > button:hover {{ background-color: {rgba(glow, 0.08)}; }}
    columnview > listview > row {{
      border-bottom: 1px solid {rgba(fg, 0.05)};
      padding: 0;
      min-height: 0;
    }}
    columnview > listview > row > cell {{
      padding: 3px 8px;
    }}
    columnview > listview > row:hover {{ background-color: {rgba(glow, 0.06)}; }}
    columnview > listview > row:selected {{
      background-color: {rgba(glow, 0.18)};
      color: {c['bright_foreground']};
      box-shadow: inset 3px 0 0 {glow};
    }}
    columnview > listview > row:selected label {{ color: {c['bright_foreground']}; }}
    columnview > listview > row:selected .ns-dim {{ color: {rgba(c['bright_foreground'], 0.65)}; }}
    columnview > listview > row:selected .ns-accent, columnview > listview > row:selected .ns-ip {{ color: {c['bright_foreground']}; }}
    .ns-cell-ip {{ color: {glow}; font-weight: 700; }}
    .ns-cell-flag {{
      color: {bg};
      background-color: {glow};
      font-weight: 800;
      font-size: {size - 3}pt;
      padding: 0 5px;
      letter-spacing: 1px;
    }}
    .ns-cell-flag.gw {{ background-color: {warn}; }}
    .ns-cell-flag.arp {{ background-color: {muted}; }}
    .ns-cell-mark {{
      font-size: {size - 3}pt;
      font-weight: 800;
      letter-spacing: 1px;
      padding: 0 5px;
    }}
    .ns-cell-mark.new {{ color: {bg}; background-color: {bad}; }}
    .ns-cell-mark.trust {{ color: {rgba(good, 0.9)}; }}
    .ns-cell-mark.unkn {{ color: {rgba(warn, 0.85)}; }}
    columnview > listview > row:selected .ns-cell-mark.trust {{ color: {c['bright_foreground']}; }}
    columnview > listview > row:selected .ns-cell-mark.unkn {{ color: {c['bright_foreground']}; }}

    .ns-inv-badge {{
      font-weight: 800;
      letter-spacing: 2px;
      font-size: {size - 2}pt;
      padding: 1px 8px;
      min-height: 0;
      border: 1px solid transparent;
      background-image: none;
      box-shadow: none;
    }}
    .ns-inv-badge:hover {{ border-color: {glow}; }}
    .ns-inv-badge.alert {{ color: {bg}; background-color: {warn}; }}
    .ns-inv-badge.ok {{ color: {rgba(good, 0.85)}; border-color: {rgba(good, 0.4)}; }}
    button.ns-watch:checked {{
      color: {bg};
      background-color: {glow};
      border-color: {glow};
    }}

    scrollbar {{ background: transparent; border: none; }}
    scrollbar slider {{
      background-color: {rgba(glow, 0.35)};
      border-radius: 0;
      min-width: 4px;
      min-height: 4px;
      border: none;
    }}
    scrollbar slider:hover {{ background-color: {rgba(glow, 0.7)}; }}
    scrolledwindow undershoot, scrolledwindow overshoot {{ background: none; }}

    paned > separator {{
      background-color: {rgba(glow, 0.28)};
      min-height: 5px;
      min-width: 5px;
      background-image: none;
    }}
    paned > separator:hover {{
      background-color: {rgba(glow, 0.7)};
    }}
    paned.horizontal > separator {{
      background-image: linear-gradient(to bottom, transparent, {rgba(glow, 0.5)}, transparent);
    }}

    .ns-log {{
      background-color: {c['darker_background']};
      color: {rgba(fg, 0.75)};
      font-size: {size - 1}pt;
      border-top: 1px solid {rgba(glow, 0.3)};
      padding: 4px 10px;
    }}
    .ns-log text {{ background-color: {c['darker_background']}; color: {rgba(fg, 0.75)}; }}
    .ns-footer {{
      background-color: {bg1};
      border-top: 1px solid {rgba(glow, 0.2)};
      padding: 4px 12px;
    }}
    .ns-tagline {{
      color: {rgba(glow, 0.85)};
      letter-spacing: 2px;
      font-size: {size - 2}pt;
      font-weight: 700;
    }}
    .ns-cursor {{ color: {glow}; }}
    .ns-cursor.off {{ color: transparent; }}

    .ns-scanlines {{
      background-image: repeating-linear-gradient(
        0deg,
        rgba(0,0,0,0.16) 0px,
        rgba(0,0,0,0.16) 1px,
        rgba(0,0,0,0.0) 1px,
        rgba(0,0,0,0.0) 3px
      );
    }}
    .ns-vignette {{
      background-image: radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,0.35) 100%);
    }}
    .ns-empty {{
      color: {rgba(muted, 0.8)};
      letter-spacing: 2px;
      padding: 30px;
    }}
    .ns-target {{
      color: {c['bright_foreground']};
      font-weight: 800;
      font-size: {size + 3}pt;
      letter-spacing: 1px;
    }}
    tooltip {{
      background-color: {bg1};
      color: {fg};
      border: 1px solid {rgba(glow, 0.5)};
      border-radius: 0;
    }}
    """

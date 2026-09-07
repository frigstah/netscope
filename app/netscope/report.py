"""Inventory / network reports in several formats. Developed for and by frig.

Reads the persistent inventory and the last scan and renders a report as JSON,
CSV, Markdown, or a self-contained HTML page. `sanitized=True` masks the public
IP and location so a report is safe to share.
"""

from __future__ import annotations

import csv
import html
import io
import json
import time
from dataclasses import asdict

from . import core, store


def _gather(refresh_public: bool = False) -> dict:
    devices = [asdict(d) for d in store.load().values()]
    devices.sort(key=lambda d: (not d["present"], -(d.get("risk") or -1),
                                 [int(x) for x in d["ip"].split(".") if x.isdigit()] or [0]))
    scan = core.last_scan() or {}
    pub = asdict(core.public_info(force=refresh_public))
    return {
        "app": core.APP_NAME, "version": core.VERSION, "tagline": core.TAGLINE,
        "generated": time.time(),
        "network": scan.get("network", ""),
        "public": pub,
        "wifi": core.wifi_status(),
        "devices": devices,
        "events": store.recent_events(100),
    }


def _mask(data: dict) -> dict:
    p = data.get("public") or {}
    for k in ("ipv4", "ipv6", "hostname", "city", "region"):
        if p.get(k):
            p[k] = "•••"
    if data.get("wifi"):
        for k in ("ssid", "bssid"):
            if data["wifi"].get(k):
                data["wifi"][k] = "•••"
    return data


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #

def _flags(d: dict) -> str:
    return "".join([
        "S" if d.get("is_self") else ("G" if d.get("is_gateway")
             else ("T" if d.get("trusted") else "U")),
        "R" if d.get("randomized") else "",
    ])


def _ports_str(d: dict) -> str:
    p = d.get("ports") or {}
    return " ".join(sorted(p.keys(), key=lambda x: int(x) if str(x).isdigit() else 0))


def as_json(data: dict) -> str:
    return json.dumps(data, indent=2)


def as_csv(data: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ip", "mac", "name", "vendor", "type", "flags", "present",
                "trusted", "risk", "open_ports", "first_seen", "last_seen"])
    for d in data["devices"]:
        w.writerow([
            d["ip"], d["mac"], d.get("name") or d.get("hostname", ""), d.get("vendor", ""),
            d.get("dtype", ""), _flags(d), d.get("present"), d.get("trusted"),
            d.get("risk") if d.get("risk", -1) >= 0 else "", _ports_str(d),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(d.get("first_seen", 0))),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(d.get("last_seen", 0))),
        ])
    return buf.getvalue()


def as_markdown(data: dict) -> str:
    p = data.get("public") or {}
    w = data.get("wifi")
    L = []
    L.append(f"# {data['app']} report")
    L.append("")
    L.append(f"Generated {time.strftime('%Y-%m-%d %H:%M', time.localtime(data['generated']))} · "
             f"{data['app']} v{data['version']} · _{data['tagline']}_")
    L.append("")
    L.append("## Overview")
    L.append("")
    present = [d for d in data["devices"] if d["present"]]
    unknown = [d for d in present if not d.get("trusted") and not d.get("is_self")]
    L.append(f"- Network: {data.get('network') or 'n/a'}")
    L.append(f"- Devices: {len(present)} present, {len(unknown)} unknown, {len(data['devices'])} known")
    if p.get("ipv4"):
        loc = ", ".join(x for x in (p.get("city"), p.get("country")) if x)
        L.append(f"- Public: {p['ipv4']} ({p.get('org','')}{' · ' + loc if loc else ''})")
    if w:
        L.append(f"- Wi-Fi: {w.get('ssid','')} · {w.get('band','')} · {w.get('signal_pct','?')}% · {w.get('security','')}")
    L.append("")
    L.append("## Devices")
    L.append("")
    L.append("| IP | Name | Type | Vendor | Flags | Risk | Open ports |")
    L.append("|---|---|---|---|---|---|---|")
    for d in data["devices"]:
        risk = d.get("risk", -1)
        L.append("| {ip} | {name} | {dt} | {ven} | {fl} | {rk} | {po} |".format(
            ip=d["ip"], name=(d.get("name") or d.get("hostname") or "").replace("|", "/"),
            dt=d.get("dtype", ""), ven=(d.get("vendor", "") or "").replace("|", "/"),
            fl=_flags(d), rk=risk if risk >= 0 else "", po=_ports_str(d)))
    if data.get("events"):
        L.append("")
        L.append("## Recent events")
        L.append("")
        for e in data["events"][:30]:
            t = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts", 0)))
            L.append(f"- `{t}` **{e['type']}** {e.get('ip','')} {e.get('name','')} {e.get('detail','')}".rstrip())
    L.append("")
    L.append("_Flags: S self · G gateway · T trusted · U unknown · R randomized MAC_")
    return "\n".join(L)


_RISK_COLOR = {"high": "#e0405a", "elevated": "#e0a53f", "low": "#5fbf7f", "clean": "#5fbf7f"}


def as_html(data: dict) -> str:
    from . import assess
    p = data.get("public") or {}
    w = data.get("wifi")
    rows = []
    for d in data["devices"]:
        risk = d.get("risk", -1)
        lvl = assess.level_for(risk) if risk >= 0 else ""
        color = _RISK_COLOR.get(lvl, "#666")
        rk = f'<span style="color:{color};font-weight:700">{risk}</span>' if risk >= 0 else "·"
        cls = "present" if d["present"] else "gone"
        rows.append(
            f'<tr class="{cls}"><td class="ip">{html.escape(d["ip"])}</td>'
            f'<td>{html.escape(d.get("name") or d.get("hostname") or "")}</td>'
            f'<td>{html.escape(d.get("dtype",""))}</td>'
            f'<td class="dim">{html.escape(d.get("vendor",""))}</td>'
            f'<td class="mono">{html.escape(d.get("mac",""))}</td>'
            f'<td>{_flags(d)}</td><td>{rk}</td>'
            f'<td class="mono dim">{html.escape(_ports_str(d))}</td></tr>')
    meta = []
    if p.get("ipv4"):
        loc = ", ".join(x for x in (p.get("city"), p.get("country")) if x)
        meta.append(f"Public {html.escape(p['ipv4'])} · {html.escape(p.get('org',''))} {html.escape(loc)}")
    if w:
        meta.append(f"Wi-Fi {html.escape(w.get('ssid',''))} · {html.escape(w.get('band',''))} · {w.get('signal_pct','?')}% · {html.escape(w.get('security',''))}")
    gen = time.strftime('%Y-%m-%d %H:%M', time.localtime(data['generated']))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(data['app'])} report</title>
<style>
 body{{background:#0a0a0a;color:#d8d8d8;font-family:ui-monospace,'JetBrainsMono Nerd Font',monospace;margin:0;padding:24px;font-size:13px}}
 h1{{color:#7fd7ff;letter-spacing:4px;font-size:20px;margin:0 0 4px}}
 .sub{{color:#7a7a7a;margin-bottom:16px}}
 .meta{{color:#9a9a9a;margin-bottom:16px;line-height:1.6}}
 table{{border-collapse:collapse;width:100%}}
 th{{text-align:left;color:#7fd7ff;border-bottom:1px solid #2a3a44;padding:6px 10px;letter-spacing:1px;font-size:11px}}
 td{{padding:5px 10px;border-bottom:1px solid #161616}}
 tr.gone{{opacity:.45}}
 .ip{{color:#7fd7ff;font-weight:700}} .dim{{color:#7a7a7a}} .mono{{font-variant-numeric:tabular-nums}}
 .foot{{color:#5a5a5a;margin-top:18px;letter-spacing:2px}}
</style></head><body>
<h1>◢ {html.escape(data['app'])} REPORT</h1>
<div class="sub">{gen} · v{html.escape(data['version'])} · network {html.escape(data.get('network') or 'n/a')}</div>
<div class="meta">{'<br>'.join(meta)}</div>
<table><thead><tr><th>IP</th><th>Name</th><th>Type</th><th>Vendor</th><th>MAC</th><th>Flags</th><th>Risk</th><th>Open ports</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<div class="foot">DEVELOPED FOR AND BY FRIG · flags: S self · G gateway · T trusted · U unknown · R randomized</div>
</body></html>"""


RENDERERS = {"json": as_json, "csv": as_csv, "md": as_markdown,
             "markdown": as_markdown, "html": as_html}


def build(fmt: str, sanitized: bool = False, refresh_public: bool = False) -> str:
    data = _gather(refresh_public=refresh_public)
    if sanitized:
        data = _mask(data)
    render = RENDERERS.get(fmt.lower())
    if not render:
        raise ValueError(f"unknown format: {fmt}")
    return render(data)

<div align="center">

# NetScope

**LAN reconnaissance, one click from your Omarchy bar.**

Local IP per interface, your public IP with ISP and location, every unit on the
network, a TCP port probe for any of them, and an AI investigation that names
the device and explains what each open port is for — in a window themed to
whatever Omarchy theme you are running.

<sub>Developed for and by frig.</sub>

</div>

![NetScope window](preview.png)

---

## What it does

A bar widget with a glance popout, and a full window for the real work. The
popout is a thin face over the command line (`bin/netscope`); everything it
shows comes from `netscope --status --json`, so the bar and the app always
agree. Everything runs unprivileged — `ip`, `ping`, `avahi-resolve`, `curl`
and plain sockets. No root, no `nmap`.

- **Interfaces** — every network card with its IPv4/IPv6, MAC, state and
  gateway. Click an address to copy it.
- **Public** — your public IPv4/IPv6, ISP, ASN and rough location, cached ten
  minutes with a refresh on demand.
- **Hosts** — every unit on the chosen network. A ping sweep plus the ARP
  table, so devices that ignore ping still show up, with reverse-DNS, mDNS
  names and MAC-vendor lookup.
- **Probe** — pick a host and probe its TCP ports. Profiles: Quick (~150
  ports), Common (1-1024 plus well-known high ports), Full (all 65535) or a
  custom list like `22,80,8000-8100`. Grabs service banners where it can.
- **AI SCAN** — fingerprints the selected device (open ports, banners, mDNS
  services, HTTP titles, TTL) and streams an AI report that identifies the
  manufacturer and model, says what the device is, and explains what each open
  port is being used for. Uses whichever AI CLI is installed (`claude`, then
  `gemini`, then `codex`); the button stays disabled if none is present.

## Requirements

- `python3`, `python-gobject`, `gtk4`, `libadwaita` (`omarchy pkg add python-gobject gtk4 libadwaita`)
- `iproute2`, `iputils` (ping), `curl` — present on stock Omarchy
- `avahi` for mDNS names (optional)
- an AI CLI — `claude`, `gemini` or `codex` — for the AI SCAN button (optional)

## Install

```bash
omarchy plugin add https://github.com/frigstah/netscope.git --enable
~/.config/omarchy/plugins/io.github.frigstah.netscope/install.sh
```

`omarchy plugin add` clones the plugin and enables the bar widget; `install.sh`
links the `netscope` command into `~/.local/bin`, installs the desktop launcher
and icon, and checks dependencies. For a private repo, make sure your git
credentials can reach it (SSH key or a token) before running `plugin add`.

## Bar widget

- **left** — popout (interfaces, public IP, last sweep)
- **right** — open the NetScope window
- **middle** — background LAN sweep
- popout keys — `o` open, `s` sweep, `r` refresh, `c` copy public IP, arrows + enter

Settings live under the `io.github.frigstah.netscope` entry in `~/.config/omarchy/shell.json`:
`pollSeconds` and `autoScanOnOpen`.

## Window

- `F5` sweep · `Enter` or `ctrl+P` probe the selected host · `ctrl+I` AI scan
- `Esc` stop · `ctrl+C` copy target IP · `ctrl+R` refresh · `ctrl+Q` quit
- double-click a host to probe it
- two draggable dividers set the proportions: interfaces vs hosts, and hosts vs probe

## Command line

```
netscope                    open the window
netscope --status [--json]  interfaces, public IP, last sweep
netscope --scan [CIDR]      sweep (default: the default-route network)
netscope --probe IP --ports quick|common|full|22,80,8000-8100
```

`--json` on any of those gives machine-readable output.

## Privacy

The LAN sweep, port probe and fingerprint stay on your machine. The AI SCAN
sends the collected evidence about the selected device to whichever AI CLI you
have installed, which talks to that tool's own provider — so that one feature
leaves your machine by design. Skip it if you want everything local.

## Layout

```
io.github.frigstah.netscope/
├── manifest.json      plugin manifest (bar-widget)
├── Panel.qml          bar icon + popout
├── install.sh         links the CLI, installs the launcher, checks deps
├── icon.png · preview.png
├── netscope.desktop   desktop launcher
├── bin/netscope       launcher (symlinked to ~/.local/bin/netscope)
└── app/netscope/      python package
    ├── core.py        interfaces, public IP, sweep, probe
    ├── recon.py       device fingerprint
    ├── ai.py          AI investigation (claude / gemini / codex)
    ├── theme.py       Omarchy colours + font -> GTK CSS
    ├── gui.py         GTK4 window
    └── cli.py         command line
```

## Uninstall

```bash
omarchy plugin remove io.github.frigstah.netscope
rm -f ~/.local/bin/netscope ~/.local/share/applications/netscope.desktop
```

---

<div align="center"><sub>Developed for and by frig.</sub></div>

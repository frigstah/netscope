# Changelog

Notable changes, newest first.

## [1.1.0] — 2026-09-08

Continuous monitoring.

### Added
- Persistent device inventory in `~/.local/state/netscope/`, keyed by MAC:
  remembers every device, tracks presence, and detects randomized MACs.
- Name devices and mark them trusted from the probe row; untrusted present
  devices show an `UNKN` mark and a `⚠ N` count badge, newly-seen devices `NEW`.
- WATCH toggle: keeps sweeping and fires desktop notifications when an unknown
  device joins or a new open port appears on a known host.
- Headless watcher (`netscope --watch`) and a systemd --user service
  (`install.sh --watch`) so alerts fire with the window closed.
- New CLI: `--watch`, `--inventory`, `--events`; `--scan`/`--status` now record
  and report the inventory. The bar icon tints while unknown devices are present.

## [1.0.0] — 2026-09-07

First release. Developed for and by frig.

### Added
- Bar widget and popout: interfaces with addresses, public IP, and the last
  LAN sweep at a glance. Left click popout, right click opens the window,
  middle click sweeps.
- NetScope window (GTK4 + libadwaita) themed from the current Omarchy colours
  and font: interfaces, public IP with ISP/ASN/geo, a live LAN host sweep
  (ping + ARP, reverse-DNS, mDNS, MAC vendor), and a TCP port probe with
  service names and banners.
- AI SCAN: fingerprints the selected device (ports, banners, mDNS services,
  HTTP titles, TTL) and streams an AI investigation report identifying the
  device and explaining each open port. Uses whichever AI CLI is installed
  (claude, gemini or codex).
- Command line: `netscope --status/--scan/--probe`, each with `--json`.
- Draggable dividers between the interfaces sidebar and the hosts area, and
  between the host list and the probe panel.

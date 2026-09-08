# Changelog

Notable changes, newest first.

## [1.7.0] - 2026-09-08

Security, privacy and correctness pass ahead of a public release.

### Security
- A device controls its own UPnP `LOCATION`, and it was fetched unrestricted, so
  a device on the LAN could point NetScope at `file:///...` (or an unrelated
  host) and have that content become its "identity" - and then AI evidence.
  Descriptions are now only fetched over http(s), only from the device that
  answered, with redirects and response size bounded.
- CSV exports neutralize spreadsheet formula injection: a device name off the
  network beginning with `=`, `+`, `-` or `@` is no longer run as a formula.

### Privacy
- The AI window no longer starts a request before the engine is settled. With
  both a cloud CLI and a local Ollama available it waits for a choice and
  remembers it, so nothing reaches a cloud provider by default.
- `--sanitized` reports now mask every globally-routable address (including
  IPv6 and addresses inside events), hostnames and MAC suffixes. Private LAN
  addresses are kept.
- Documented the automatic third-party public-IP and location lookup.

### Fixed
- AI cancellation kills the whole process group, so STOP works even when the
  CLI spawned helpers holding its pipes; RERUN cancels the previous run instead
  of interleaving two reports.
- Inventory survives a damaged file (wrong shape or invalid JSON is quarantined,
  not silently dropped).
- Learning a device's MAC no longer creates a second record and orphan its name
  and trust setting.
- IPv6-only devices are now aged toward "gone"; probing a narrower port range no
  longer discards the wider baseline and then reports old ports as new.
- SNMP replies are bounds-checked, NetBIOS parsing honours the header counts,
  IPv6 web/HTTP URLs are bracketed, and an SSID containing ":" no longer shifts
  the Wi-Fi fields.

### Changed
- Removed the unused `grim` dependency; documented minimum Python/GTK versions;
  corrected the watch and icon-tinting claims in the README.

## [1.6.3] - 2026-09-08

### Changed
- The bar icon now follows the theme colour by default. A new "Tint icon on
  unknown device" setting (off by default) makes it turn urgent while an
  untrusted device is present.
- Replaced em dashes with hyphens throughout the docs and UI text.

## [1.6.2] - 2026-09-08

### Fixed
- Notification flood: devices with randomized/private MACs (phones, some IoT)
  rejoin under a new MAC each sweep and looked "new" every time; they no longer
  raise device-joined/left desktop notifications.
- Marking a device trusted without typing a name no longer crashes.
- Concurrency: inventory writes are serialized across GUI threads and the
  headless watcher (file lock + atomic temp-file writes, no leftover .tmp).
- Presence: a device is only aged toward "gone" when its own subnet was
  actually swept, so scanning a different range no longer marks it gone.
- Aborted sweeps no longer overwrite the cached last scan; the GUI records a
  probe's ports/risk to the inventory (was only done from the CLI).
- AI streaming reads stdout and stderr concurrently (no pipe deadlock), stays
  responsive to STOP, and always reaps the process and closes its pipes.
- Sweep builds its target list lazily (large CIDRs) and keeps IPv6-only hosts
  that discovery adds. TUI selection index is clamped against list changes.

## [1.6.1] - 2026-09-08

### Fixed
- Marking a device trusted now updates the row immediately: list cells are
  reactive to property changes (STATUS, name, and flag columns), and the TRUST
  toggle shows a clear checked state.
- The device STATUS column (UNKN / TRUST / NEW) is now labeled so the "unknown"
  status is self-explanatory.

### Changed
- Bar icon renders through OpticalGlyph so it sits at the same height as the
  other bar icons; a new "Bar icon" setting picks the glyph (access-point,
  radar, wifi, crosshairs, lan) - default access-point.

## [1.6.0] - 2026-09-08

Reports, actions, and a terminal UI.

### Added
- `report.py`: export the inventory as JSON, CSV, Markdown, or a self-contained
  HTML page. GUI `ctrl+E`, CLI `netscope --report FMT [--out FILE] [--sanitized]`.
- `actions.py`: per-device `⋯` menu in the window - open web UI, SSH, Wake-on-LAN
  (`core.wake_on_lan`, `netscope --wake MAC`), ping, copy IP.
- `tui.py`: a dependency-free curses terminal UI (`netscope --tui`) with
  interfaces, Wi-Fi, public IP, a live host list, sweep, probe and watch.

## [1.5.0] - 2026-09-08

IPv6 and Wi-Fi context.

### Added
- Wi-Fi panel in the sidebar (SSID, signal %/dBm, band, channel, rate,
  security) from nmcli + iw; `netscope --wifi` and a `wifi` field in `--status`.
- IPv6: sweeps read the v6 neighbour table (ping ff02::1), attach global v6
  addresses to hosts by MAC, add v6-only neighbours, and tag hosts `+v6`.

## [1.4.0] - 2026-09-08

Device identification.

### Added
- `discover.py`: unprivileged active discovery - SSDP/UPnP (friendly name,
  model, device type), NetBIOS name query, and SNMP `sysDescr` (public).
- A device-type heuristic (router, NAS, camera, printer, media, IoT, VM,
  file server, …) combining discovery with vendor and open ports.
- Discovery and the type guess are folded into the AI fingerprint and stored
  on the device record. New CLI `netscope --identify IP`.

## [1.3.0] - 2026-09-08

Local AI and whole-network summary.

### Added
- Local **Ollama** engine for AI SCAN (http://localhost:11434), so an
  investigation can run fully on-device. The AI window has an engine selector
  (cloud CLI or local) and a RERUN button. `NETSCOPE_OLLAMA_HOST` /
  `NETSCOPE_OLLAMA_MODEL` override the endpoint and model.
- Whole-network AI summary: click the unknown-count badge or press
  `ctrl+shift+I` for one report that inventories the LAN, ranks risks, and
  recommends fixes, built from the stored inventory and per-device findings.

## [1.2.0] - 2026-09-08

Security posture.

### Added
- `assess.py`: a rules engine that grades a probed device's exposure and
  returns findings plus a 0-100 risk score (plaintext admin, unauthenticated
  databases, risky management ports, UPnP, broad open-port surface).
- GUI: a `RISK N` badge and a findings popover appear after each probe; the
  score is stored on the device record.
- CLI `--assess IP`, and `--probe --json` now includes the assessment.
- The findings are included in the AI investigation prompt so the model
  corroborates rather than invents.

## [1.1.0] - 2026-09-08

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

## [1.0.0] - 2026-09-07

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

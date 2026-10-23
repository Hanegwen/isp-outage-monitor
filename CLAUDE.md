# Guide for coding agents

A home internet outage monitor for a Raspberry Pi (or any Linux box with systemd). It pings the router, the
modem and public servers every 30 s, stores samples in SQLite, runs a daily Ookla speed test, emails a
monthly report for ISP credit requests, and can send data-cap alerts from a Netgear router's Traffic Meter.
README.md is the user view, docs/DEPLOY.md the install guide, docs/ARCHITECTURE.md the design.

## Layout

- `monitor/config.py` - every setting, read from environment variables with defaults
- `monitor/db.py` - SQLite schema (created on connect) and `connect()`
- `monitor/checker.py` - always-on loop, one row in `samples` per interval
- `monitor/report.py` - monthly report: text, HTML, CSV, email
- `monitor/speedtest.py` - one Ookla speed test per run
- `monitor/usage.py` - hourly Traffic Meter read and data-cap alerts
- `deploy/` - systemd units, `install.sh`, the `cm` helper (`@SERVICE_USER@` is filled in by `install.sh`)
- `pc/` - optional Windows helper that copies a Steam download summary to the monitor over SSH
- `tests/` - unittest, no network or hardware needed
- `tools/check_public.py` - fails if a tracked file holds personal details

## Run the checks

```sh
python -m unittest -v
python tools/check_public.py
```

Both must pass before every commit; CI runs both. CI sets `TZ=America/New_York` because report math uses
local time and DST matters. Optional pre-commit hook:
`printf '#!/bin/sh\nexec python tools/check_public.py --staged\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`.

## Nothing personal in this repo

The repo is public. Never commit real IPs, MAC addresses, hostnames, Tailscale names or addresses, emails,
account numbers, addresses, prices, usage figures, device names or file paths with a username. Use
placeholders: `192.168.1.1`, `192.0.2.x` / `198.51.100.x` / `203.0.113.x`, `user@example.com`, `<your-user>`,
`123 Main St`. Real settings live only in `/etc/comcast-monitor.env` on the device. If a line is really fine
but trips the check, end it with `public-ok`.

## Conventions

- Python standard library only. Python 3.9+ (CI uses 3.11). No new dependencies.
- Every setting goes in `monitor/config.py` via `_get(NAME, default)`, and is documented in
  `config.example.env` and the settings table in `docs/DEPLOY.md`.
- Modules are run as `python3 -m monitor.<name>`; each has a `main()` with argparse.
- SQL always uses `?` placeholders. Schema changes go in `db.SCHEMA` with `CREATE ... IF NOT EXISTS`;
  there are no migrations, so only add tables or nullable columns in a backward-compatible way.
- Times are stored as Unix seconds and shown in the machine's local time. Tests build times with
  naive `datetime(...)`, which also means local time.
- Tests change `config.*` attributes directly and restore them in `finally`; use `db.connect(":memory:")`.
- Short, plain comments and docs. Keep README, DEPLOY.md and ARCHITECTURE.md in step with the code.

## Deploy model

Code lives at `/opt/comcast-monitor` (paths are fixed in the units and `cm`). `deploy/install.sh` (root)
creates the service user, installs the speedtest CLI, the env file (`640`, `root:<service group>`), `cm`,
and the units, then starts them. Data is in `/var/lib/comcast-monitor` (systemd `StateDirectory`).
Updating = `git pull` + `install.sh` again.

## Gotchas

- `lan_down` (router unreachable) is never counted against the ISP; `isp_down` and `dns_down` are.
- The checker retries a failed probe once after 3 s; the row keeps the first try's timestamp.
- Gaps over 3 x `CHECK_INTERVAL` are `monitor_offline` (reduced coverage), not outages.
- The R7000 allows one admin session: `usage.fetch()` raises `RouterBusy` and the hour is skipped. It
  always signs out. The router's month counter sometimes reads low for hours, so the hourly spike alert
  compares against the highest reading of the last 24 h.
- Alert keys in `usage_alerts` (`cap90:YYYY-MM`, `daily:YYYY-MM-DD`, ...) make each alert send once.
- `cm` sources the env file with `sh`, while systemd parses it itself: keep values simple and quote spaces.
- The services and `cm` switch to the service user; never run them as root.

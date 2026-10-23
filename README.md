# Comcast Outage Monitor

A small always-on monitor for Comcast/Xfinity home internet. It runs on a Raspberry Pi (or any Linux box
wired to your router), records every time the internet goes down, runs a daily speed test, and on the 1st of
each month emails you a report of the previous month: outage times, total downtime, uptime, speed test
results, and a ready-to-paste message for requesting a credit from Xfinity.

Not affiliated with Comcast or Xfinity. It works with any ISP; the wording just assumes Xfinity.

## Features

- Pings your router, your cable modem and several public servers every 30 s and tells home-network problems
  apart from ISP outages
- Monthly email report (text + HTML, CSV attachment) with an optional credit estimate
- Daily Ookla speed test, summarized in the report
- Optional hourly data-usage alerts against the Xfinity data cap (Netgear routers with Traffic Meter only)
- Python standard library only, SQLite storage, systemd services and timers

## How it decides "Comcast is down"

Every 30 seconds the monitor pings:

| Target | Meaning when it fails |
|---|---|
| Router (`GATEWAY_IP`, default `192.168.1.1`) | Home network problem. Logged as `lan_down`, **not** counted against Comcast |
| Cable modem (`MODEM_IP`, default `192.168.100.1`) | Noted on the outage ("modem unreachable") |
| `1.1.1.1`, `8.8.8.8`, `9.9.9.9` (`INTERNET_TARGETS`) | If all fail while the router is up → `isp_down` |
| DNS lookup of `www.google.com` (`DNS_TEST_HOST`) | If it fails while the pings work → `dns_down` |

A failure is retried once (3 s later) before it is logged. Gaps where the monitor itself wasn't running
(power outage, reboot) are shown as reduced *coverage*, not as outages. Outages shorter than
2 minutes (`MIN_OUTAGE_SECONDS`, default 120) are listed separately as "brief drops". Both `isp_down` and
`dns_down` count against the ISP.

For how the pieces fit together, with diagrams, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Data-usage alerts (optional)

Only for Netgear routers that have a **Traffic Meter** page (tested with an R7000). Every hour the monitor
signs in to the router, reads the Traffic Meter (total upload + download), and signs out. It emails when:

- this month passes **50 / 75 / 90 / 100 %** of the cap (`DATA_CAP_GB`, default 1200 = Xfinity's 1.2 TB)
- the month is **on pace** to go over the cap (from the 5th on)
- one day uses more than `DAILY_ALERT_GB` (default 60), or one hour more than `HOURLY_ALERT_GB` (default 15)

**Steam game updates (optional, Windows).** The router only sees the whole house, so a PC can tell the monitor
what Steam downloaded: `pc/steam-report.ps1` reads Steam's download log and copies a summary over SSH to
`/var/lib/comcast-monitor/steam.json` every 5 minutes (scheduled task set up once with `pc/install-task.ps1`;
`STEAM_REPORT` changes the path on the monitor side; setup in
[docs/DEPLOY.md](docs/DEPLOY.md#optional-steam-helper-windows)). If Steam explains a spike, the email is a calm "Steam game update: <game>"
note instead of the heavy-use warning; if the rest of the traffic is still over the limit, the warning is sent
and says how much was Steam.

Each alert is sent once (per month / per day). If someone is signed in to the router page, that hour is
skipped (the R7000 allows one admin session at a time). The router's count can differ a little from
Xfinity's meter, which is the official one (Xfinity app > Internet > Data usage).

Turn it on by setting `ROUTER_USER` / `ROUTER_PASSWORD`; with no password the hourly job just exits.
**Note:** Netgear's admin page uses HTTP basic auth over plain HTTP, so the password crosses your LAN
unencrypted. Use a router password you use nowhere else and keep the router's remote management off.

## Requirements

- A Raspberry Pi or any always-on Linux machine, **wired** to your router with Ethernet
  (tested with a Netgear R7000 router and a Netgear CM1000v2 modem; other models work for outage monitoring)
- Python 3.9+ and systemd (Raspberry Pi OS, Debian and Ubuntu have both)
- A Gmail account with 2-Step Verification and an
  [App Password](https://support.google.com/accounts/answer/185833) to send the emails
- Internet access at install time to download the Ookla speedtest CLI (aarch64, armv7l or x86_64)

## Install

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for a step-by-step guide. In short:

```sh
sudo git clone https://github.com/Hanegwen/isp-outage-monitor /opt/comcast-monitor
cd /opt/comcast-monitor
sudo ./deploy/install.sh              # an existing install keeps its service user
sudo nano /etc/comcast-monitor.env    # fill in email settings
sudo systemctl restart comcast-monitor
```

## Handy commands

`sudo` is only needed when you aren't signed in as the service user (e.g. when `install.sh` created
`comcast-monitor`); if the services run as your own user, plain `cm report` works.

```sh
sudo cm report                     # print last month's report
sudo cm report --month 2026-10     # any month (current month = so far)
sudo cm report --send              # email it now
sudo cm speedtest                  # run a speed test now
sudo cm usage --dry-run            # show data usage + which alerts would fire
sudo cm usage --test-email         # email the current data usage
journalctl -u comcast-monitor      # status changes (ok -> isp_down etc.)
systemctl list-timers 'comcast-*'
```

## Privacy

All data stays on your machine in a SQLite file (`/var/lib/comcast-monitor/monitor.db`). Nothing is sent
anywhere except: pings and a DNS lookup to public DNS servers, the Ookla speed test, and the emails you
configure (Gmail SMTP). The data-usage feature also talks to your router on the LAN.

## Limitations

- It can only see what one wired machine sees. Wi-Fi problems, or a router that stays up while its WAN side
  is broken in odd ways, may be classified differently than you expect.
- An outage that also takes down the monitor's power is shown as reduced coverage, not as an outage.
- The credit estimate is a simple daily-rate calculation; Xfinity decides the actual credit.
- Speed tests at ~800 Mbps use roughly 1 GB each; once a day is ~30 GB/month of a data cap.
  Change `deploy/comcast-speedtest.timer` to adjust.
- Data-usage alerts only work with Netgear routers that have the Traffic Meter page.
- Times in the report use the machine's local timezone.

## Layout

```
monitor/checker.py    always-on loop -> samples table
monitor/speedtest.py  Ookla speedtest, daily at ~9 PM -> speedtests table
monitor/usage.py      hourly: reads the router's Traffic Meter, emails data-cap alerts -> usage table
monitor/report.py     builds the monthly report (text, HTML email, CSV attachment) and sends it
monitor/config.py     settings from environment variables
deploy/               systemd units, install.sh, `cm` helper
pc/                   optional Windows helper: reports Steam downloads to the monitor
tests/                unit tests (standard library unittest)
tools/check_public.py fails if a tracked file contains personal details (CI runs it)
docs/DEPLOY.md        step-by-step install guide
docs/ARCHITECTURE.md  how the pieces fit together, schema, what leaves the network
```

Settings: `/etc/comcast-monitor.env` (template: `config.example.env`).

## Tests

```sh
python -m unittest -v
python tools/check_public.py
```

No Pi, router or network is needed; the tests use an in-memory SQLite database.

## License

MIT, see [LICENSE](LICENSE). Security notes and how to report a problem: [SECURITY.md](SECURITY.md).

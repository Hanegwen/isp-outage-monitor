# Deploying the Comcast Outage Monitor

This guide assumes no prior Raspberry Pi experience. Any always-on Linux machine with systemd works the
same way; skip the Pi-specific parts.

## 1. Prepare the Pi

1. Flash **Raspberry Pi OS Lite (64-bit)** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
   In the Imager settings, set a username and password and turn on SSH.
2. Connect the Pi to your router with an **Ethernet cable**. Wi-Fi would make Wi-Fi problems look like
   outages.
3. Boot it, find its address in your router's device list, and sign in:

   ```sh
   ssh <your-user>@<pi-address>
   ```

4. Update it and install git:

   ```sh
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git curl
   ```

5. Set the timezone. Report times and the monthly schedule use the Pi's local time:

   ```sh
   timedatectl list-timezones                    # find yours, e.g. Europe/London
   sudo timedatectl set-timezone <Region/City>
   timedatectl                                   # check
   ```

## 2. Copy the code to /opt/comcast-monitor

The systemd units expect the code at exactly `/opt/comcast-monitor`.

**Option A, on the Pi (simplest):**

```sh
sudo git clone https://github.com/Hanegwen/isp-outage-monitor /opt/comcast-monitor
```

**Option B, from another computer** that has a clone (useful if you have local changes):

```sh
git archive HEAD | ssh <your-user>@<pi-address> 'sudo mkdir -p /opt/comcast-monitor && sudo tar -x -C /opt/comcast-monitor'
```

## 3. Run the installer

```sh
cd /opt/comcast-monitor
sudo ./deploy/install.sh
```

It:

- creates a system user `comcast-monitor` to run the services (to use an existing account instead:
  `sudo SERVICE_USER=<user> ./deploy/install.sh`). Running it again later keeps whichever user the
  installed services already use, so updates need no extra options.
- downloads the Ookla `speedtest` CLI for your CPU (aarch64, armv7l or x86_64) to `/usr/local/bin`
- copies `config.example.env` to `/etc/comcast-monitor.env` (only the first time)
- installs the `cm` helper to `/usr/local/bin/cm`
- installs and starts the systemd services and timers

## 4. Fill in the settings

```sh
sudo nano /etc/comcast-monitor.env
```

One `KEY=value` per line, comments only on their own line, and quote values with spaces
(`SERVICE_ADDRESS="123 Main St, Springfield"`). Blank means "use the default".

| Variable | What it is |
|---|---|
| `CUSTOMER_NAME`, `SERVICE_ADDRESS`, `ACCOUNT_NUMBER` | Optional. Shown on the report and in the copy/paste credit request |
| `PLAN_NAME` | Optional, e.g. the name of your Xfinity plan |
| `PLAN_MBPS` | Advertised download speed (default 800). Speed tests under half of it are flagged |
| `MONTHLY_CHARGE` | What you pay per month, e.g. `80.00`. Enables the credit estimate; blank = no estimate |
| `PROMO_CHARGE`, `PROMO_FROM`, `PROMO_UNTIL`, `PROMO_NOTE` | Optional temporary price used for report months `PROMO_FROM`..`PROMO_UNTIL` (YYYY-MM, inclusive) |
| `REPORT_TO` | Address that receives the emails |
| `SMTP_USER` | The Gmail address that sends them |
| `SMTP_APP_PASSWORD` | A Gmail [App Password](https://support.google.com/accounts/answer/185833) (16 letters; needs 2-Step Verification). Not your normal password |
| `CHECK_INTERVAL` | Seconds between checks (default 30) |
| `MIN_OUTAGE_SECONDS` | Shorter outages are listed as "brief drops" (default 120) |
| `GATEWAY_IP` | Your router's LAN address (default `192.168.1.1`), see Troubleshooting |
| `MODEM_IP` | Your cable modem's address (default `192.168.100.1`) |
| `INTERNET_TARGETS` | Public servers to ping, comma separated (default `1.1.1.1,8.8.8.8,9.9.9.9`) |
| `ROUTER_USER`, `ROUTER_PASSWORD` | Optional, Netgear Traffic Meter routers only. Blank password = data-usage alerts off |
| `DATA_CAP_GB` | Monthly data allowance (default 1200) |
| `DAILY_ALERT_GB`, `HOURLY_ALERT_GB` | Alert thresholds for one day / one hour (defaults 60 / 15) |
| `STEAM_REPORT` | Where the optional Steam helper's summary is read from (default `/var/lib/comcast-monitor/steam.json`) |

Advanced, commented out at the end of the template: `DB_PATH` (default `/var/lib/comcast-monitor/monitor.db`),
`DNS_TEST_HOST` (default `www.google.com`), `SMTP_HOST` / `SMTP_PORT` (default `smtp.gmail.com` / `465`, SSL).

The file holds passwords, so it must stay readable only by root and the service user. The installer sets
this; to check or fix it by hand:

```sh
ls -l /etc/comcast-monitor.env        # -rw-r----- root comcast-monitor
sudo chown root:comcast-monitor /etc/comcast-monitor.env && sudo chmod 640 /etc/comcast-monitor.env
```

Then restart the checker so it picks up the changes (the timers read the file each time they run):

```sh
sudo systemctl restart comcast-monitor
```

## 5. Test it

`cm` runs a module by hand with the service's settings. Use `sudo`; it switches to the service user.

```sh
# sudo is only needed when you aren't the service user
sudo cm report --month $(date +%Y-%m)   # this month so far (expect low coverage on day one)
sudo cm report                          # last month (all "before monitoring began" at first)
sudo cm speedtest                       # one speed test; prints the stored row
sudo cm usage --dry-run                 # data usage + alerts that would fire (or "ROUTER_PASSWORD not set")
sudo cm usage --test-email              # email the current data usage (needs ROUTER_PASSWORD)
sudo cm report --send                   # email last month's report: checks the Gmail settings
sudo cm report --month 2026-10 --out /tmp/report.html   # save the HTML version
```

Check the services and timers:

```sh
systemctl status comcast-monitor
systemctl list-timers 'comcast-*'
journalctl -u comcast-monitor -f        # status changes, e.g. "status ok -> isp_down"
journalctl -u comcast-report -u comcast-speedtest -u comcast-usage --since today
```

Schedule: the checker runs all the time, the speed test daily around 9 PM, data usage hourly, and the
report at 8 AM on the 1st (retried every 30 minutes if sending fails).

## Optional: Steam helper (Windows)

Only useful with data-usage alerts on. `pc/steam-report.ps1` runs on a Windows PC every 5 minutes, reads
Steam's download log and copies a small JSON summary to the monitor over SSH. It sends byte counts, game
names, timestamps and the PC's computer name.

1. The SSH user on the monitor must be able to write `/var/lib/comcast-monitor`. That directory belongs to
   the service user, and the default `comcast-monitor` user can't sign in, so install with your own login
   user: `sudo SERVICE_USER=<your-user> ./deploy/install.sh`.
2. On the PC, set up key login without a passphrase (`ssh-keygen`, then add the public key to
   `~/.ssh/authorized_keys` on the monitor). Restrict that key to the one command it needs by starting its
   line in `authorized_keys` with:

   ```
   command="cat > /var/lib/comcast-monitor/steam.json.tmp && mv /var/lib/comcast-monitor/steam.json.tmp /var/lib/comcast-monitor/steam.json",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty
   ```

3. Test it once: `powershell -File pc\steam-report.ps1 -PiHost <your-user>@<pi-address>` (the default
   host is an SSH alias named `pi`). Pass `-SteamDir` if Steam isn't in `C:\Program Files (x86)\Steam`.
4. Register the scheduled task: `powershell -File pc\install-task.ps1`. It runs `run-hidden.vbs`, which
   runs the script with its defaults, so add a `pi` entry to `~/.ssh/config` on the PC or edit the defaults.

The remote path is fixed in the script; if you change `STEAM_REPORT`, change the script too.

## Updating

```sh
cd /opt/comcast-monitor
sudo git pull                       # or repeat the git archive command from step 2
sudo ./deploy/install.sh            # keeps the user the services already run as
```

Your `/etc/comcast-monitor.env` and the database are kept.

## Uninstalling

```sh
sudo systemctl disable --now comcast-monitor.service comcast-speedtest.timer comcast-report.timer comcast-usage.timer
sudo rm /etc/systemd/system/comcast-*.service /etc/systemd/system/comcast-*.timer
sudo systemctl daemon-reload
sudo rm -rf /opt/comcast-monitor /usr/local/bin/cm /etc/comcast-monitor.env
sudo rm -rf /var/lib/comcast-monitor     # the data; keep it if you may want the history
sudo userdel comcast-monitor             # only if install.sh created it
sudo rm /usr/local/bin/speedtest         # optional
```

## Troubleshooting

- **Everything shows `lan_down`.** `GATEWAY_IP` is wrong. Many routers are not `192.168.1.1` (Xfinity
  gateways use `10.0.0.1`). Find yours with `ip route | grep default` (the address after `via`).
- **"modem unreachable" on every outage.** Most cable modems answer at `192.168.100.1`, but some don't, and
  an Xfinity gateway is modem and router in one box (set `MODEM_IP` to the same as `GATEWAY_IP`). This only
  adds a note; it never changes whether something counts as an outage.
- **Ping.** Nothing special is needed: Raspberry Pi OS and Debian let normal users ping. Check with
  `sudo -u comcast-monitor ping -c1 1.1.1.1`.
- **Gmail errors** (`SMTPAuthenticationError`, "Username and Password not accepted"): use an App Password,
  not your normal password; 2-Step Verification must be on; `SMTP_USER` must be the full Gmail address; check
  for typos or stray spaces in the value. `REPORT_TO / SMTP_USER / SMTP_APP_PASSWORD not configured` means
  one of the three is blank.
- **Speed tests fail.** See the `error` in `sudo cm speedtest` output. The installer only knows aarch64,
  armv7l and x86_64 builds; for others install the CLI from https://www.speedtest.net/apps/cli. The monitor
  runs it with `--accept-license --accept-gdpr`, which accepts Ookla's license and privacy terms (personal,
  non-commercial use); read them if that matters to you.
- **Data usage: "could not read the Traffic Meter page".** Wrong `ROUTER_USER` / `ROUTER_PASSWORD`, the
  router has no Traffic Meter (or it is turned off in the router's settings), or the firmware lays the page
  out differently. "router admin page in use" means someone was signed in; it tries again next hour.
- **`cannot read /etc/comcast-monitor.env - run with sudo`.** Run `cm` with `sudo`.
- **Report times are off by hours.** Set the timezone (step 1, item 5) and restart: `sudo systemctl restart comcast-monitor`.

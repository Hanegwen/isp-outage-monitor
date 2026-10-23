"""Data-usage monitor: reads the Netgear router's Traffic Meter and emails when usage nears the Xfinity cap.

Runs hourly (comcast-usage.timer). Alerts, each sent at most once:
  - month total passes 50 / 75 / 90 / 100 % of DATA_CAP_GB          (once per month per level)
  - month is on pace to pass the cap (from day 5 on)                (once per month)
  - more than HOURLY_ALERT_GB used since the previous reading        (once per day)
  - more than DAILY_ALERT_GB used yesterday                          (once per day)

A spike that Steam game updates on the PC explain (pc/steam-report.ps1 copies Steam's download log to
STEAM_REPORT every 5 minutes) gets a calm "Steam game update" note instead of the heavy-use warning.

The R7000 allows one admin session at a time: if someone is signed in to the router page, this
reading is skipped (we never kick them out) and we always sign out after reading.
"""
import argparse
import calendar
import http.cookiejar
import json
import re
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import config, db

ROWS = ("Today", "Yesterday", "This week", "This month", "Last month")
NEEDED = ("Today", "Yesterday", "This month", "Last month")  # rows evaluate/record use
_NUM = r"([\d.]+)(?:\s*/\s*[\d.]+)?"
ROW_RE = re.compile(r"(%s)\s+\S+\s+%s\s+%s\s+%s" % ("|".join(ROWS), _NUM, _NUM, _NUM))


class RouterBusy(Exception):
    """Someone else is signed in to the router admin page."""


def parse(html):
    """Traffic Meter HTML -> {"Today": (up_mb, down_mb, total_mb), ...}"""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return {m.group(1): tuple(float(m.group(i)) for i in (2, 3, 4)) for m in ROW_RE.finditer(text)}


def fetch():
    base = f"http://{config.GATEWAY_IP}"
    pw = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pw.add_password(None, base, config.ROUTER_USER, config.ROUTER_PASSWORD)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                         urllib.request.HTTPBasicAuthHandler(pw))
    try:
        html = ""
        for _ in range(3):  # the first answer is often a 401 that only sets the XSRF cookie
            try:
                html = opener.open(base + "/traffic_meter.htm", timeout=15).read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                continue
            if "Traffic Volume" in html or "multiLogin" in html:
                break
        if "multiLogin" in html:
            raise RouterBusy()
        rows = parse(html)
        missing = [name for name in NEEDED if name not in rows]
        if missing:
            raise RuntimeError("could not read the Traffic Meter page (wrong router password?); "
                               "missing rows: " + ", ".join(missing))
        return rows
    finally:
        try:
            opener.open(base + "/LGO_logout.htm", timeout=10).read()
        except Exception:
            pass


def gb(mb):
    return mb / 1000


def load_steam():
    try:
        with open(config.STEAM_REPORT, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def steam_between(steam, start, end, now=None):
    """GB Steam downloaded on the PC between two unix times and the games updated then.

    Steam logs its byte counts every ~5 minutes and the PC sends them every 5 minutes, so while a download
    is still running the newest few minutes are missing; those are estimated from the last 15 minutes."""
    if not steam:
        return 0.0, []
    events = [(t, b) for t, b in steam.get("events", []) if start < t <= end]
    total = sum(b for _, b in events) / 1e9
    if now and steam.get("downloading") and events:
        last = max(t for t, _ in events)
        recent = sum(b for t, b in events if t > last - 900) / 1e9
        total += recent / 900 * min(now - last, 900)
    apps = []
    for t, name in steam.get("apps", []):
        if start - 6 * 3600 < t <= end and name not in apps:
            apps.append(name)
    return total, apps


def evaluate(conn, rows, now, steam=None):
    """Decide which alerts are due. Returns [(key, subject, body)], keys not yet sent."""
    cap = config.DATA_CAP_GB
    month = now.strftime("%Y-%m")
    used = gb(rows["This month"][2])
    pct = used / cap * 100 if cap else 0
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    elapsed_days = (now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
    projected = used / elapsed_days * days_in_month if elapsed_days > 0 else used
    status = (f"Used so far in {now:%B}: {used:,.0f} GB of {cap:,.0f} GB ({pct:.0f}%)\n"
              f"On pace for: {projected:,.0f} GB by the end of the month\n"
              f"Yesterday: {gb(rows['Yesterday'][2]):,.1f} GB   Today so far: {gb(rows['Today'][2]):,.1f} GB\n"
              f"This month upload / download: {gb(rows['This month'][0]):,.0f} / {gb(rows['This month'][1]):,.0f} GB\n"
              f"Last month total: {gb(rows['Last month'][2]):,.0f} GB\n\n"
              "Numbers come from the router's Traffic Meter; Xfinity's own meter (Xfinity app > Internet > "
              "Data usage) is the official count and can differ by a few percent.")
    due = []

    for level in (100, 90, 75, 50):
        if pct >= level:
            due.append((f"cap{level}:{month}",
                        f"Data usage: {pct:.0f}% of the {cap:,.0f} GB Xfinity cap used ({now:%B})", status))
            break  # only the highest level crossed; lower ones are marked sent below

    if now.day >= 5 and projected > cap:
        due.append((f"pace:{month}", f"Data usage on pace to pass the cap: ~{projected:,.0f} GB this month", status))

    yesterday = gb(rows["Yesterday"][2])
    if yesterday > config.DAILY_ALERT_GB:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = (midnight - timedelta(days=1)).strftime("%Y-%m-%d")
        steam_gb, games = steam_between(steam, (midnight - timedelta(days=1)).timestamp(), midnight.timestamp())
        if yesterday - steam_gb > config.DAILY_ALERT_GB:
            due.append((f"daily:{day}", f"Big data day: {yesterday:,.0f} GB used yesterday",
                        _steam_line(steam_gb, games) + status))
        else:
            due.append((f"daily:{day}", f"Big data day from Steam game updates: {yesterday:,.0f} GB yesterday",
                        _steam_note(steam_gb, games, yesterday, "yesterday") + status))

    # Baseline is the highest reading of the last 24 h, not just the latest one: the R7000 has shown a
    # month total far too low for a few hours, and the jump back up looked like a spike.
    prev = conn.execute("SELECT ts, month_mb FROM usage WHERE month = ? AND ts >= ? "
                        "ORDER BY month_mb DESC, ts DESC LIMIT 1", (month, now.timestamp() - 86400)).fetchone()
    if prev:
        hours = (now.timestamp() - prev["ts"]) / 3600
        delta = gb(rows["This month"][2] - prev["month_mb"])
        if 0 < hours <= 3 and delta > config.HOURLY_ALERT_GB * hours:
            steam_gb, games = steam_between(steam, prev["ts"], now.timestamp(), now.timestamp())
            if delta - steam_gb > config.HOURLY_ALERT_GB * hours:
                due.append((f"hourly:{now:%Y-%m-%d}",
                            f"Heavy data use right now: {delta:,.0f} GB in the last {hours * 60:.0f} minutes",
                            "Something on the network is using a lot of data. If nobody is streaming/downloading "
                            "big files, check any always-on devices such as a NAS, cameras or backup jobs.\n\n" +
                            _steam_line(steam_gb, games) + status))
            else:
                due.append((f"steam:{now:%Y-%m-%d}",
                            "Steam game update: " + (", ".join(games) or "downloading") +
                            f" ({steam_gb:,.0f} GB in the last {hours * 60:.0f} minutes)",
                            _steam_note(steam_gb, games, delta, f"in the last {hours * 60:.0f} minutes") + status))

    sent = {r["key"] for r in conn.execute("SELECT key FROM usage_alerts")}
    return [a for a in due if a[0] not in sent]


def _steam_line(steam_gb, games):
    if steam_gb < 1:
        return ""
    return (f"Steam on the PC accounts for {steam_gb:,.0f} GB of it" + (f" ({', '.join(games)})" if games else "") +
            "; the rest is something else.\n\n")


def _steam_note(steam_gb, games, total, when):
    return ("Nothing to worry about: this is a normal Steam game update on the PC" +
            (f" ({', '.join(games)})" if games else "") +
            f". Steam downloaded {steam_gb:,.0f} of the {total:,.0f} GB used {when}.\n\n"
            "To keep big updates out of the daytime: Steam > Settings > Downloads > Schedule auto-updates.\n\n")


def send(subject, body):
    if not (config.REPORT_TO and config.SMTP_USER and config.SMTP_APP_PASSWORD):
        raise SystemExit("REPORT_TO / SMTP_USER / SMTP_APP_PASSWORD not configured")
    # game names come from the PC's Steam report; a newline in the subject would make the email fail
    subject = re.sub(r"[\x00-\x1f\x7f]+", " ", subject).strip()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, config.SMTP_USER, config.REPORT_TO
    msg.set_content(body)
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.login(config.SMTP_USER, config.SMTP_APP_PASSWORD)
        smtp.send_message(msg)


def record(conn, rows, now):
    conn.execute("INSERT INTO usage (ts, month, today_mb, yesterday_mb, month_mb, month_up_mb, month_down_mb,"
                 " last_month_mb) VALUES (?,?,?,?,?,?,?,?)",
                 (int(now.timestamp()), now.strftime("%Y-%m"), rows["Today"][2], rows["Yesterday"][2],
                  rows["This month"][2], rows["This month"][0], rows["This month"][1], rows["Last month"][2]))
    conn.commit()


def mark_sent(conn, key, now):
    conn.execute("INSERT OR IGNORE INTO usage_alerts (key, ts) VALUES (?, ?)", (key, int(now.timestamp())))
    if key.startswith("cap"):  # crossing 90% also covers 75% and 50%
        level, month = key[3:].split(":")
        for lower in (50, 75, 90):
            if lower < int(level):
                conn.execute("INSERT OR IGNORE INTO usage_alerts (key, ts) VALUES (?, ?)",
                             (f"cap{lower}:{month}", int(now.timestamp())))
    conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="print, don't store or email")
    p.add_argument("--test-email", action="store_true", help="email the current status")
    args = p.parse_args()

    if not config.ROUTER_PASSWORD:
        print("ROUTER_PASSWORD not set; data-usage monitoring is off")
        return
    now = datetime.now()
    try:
        rows = fetch()
    except RouterBusy:
        print("router admin page in use by someone else; skipping this reading")
        return
    conn = db.connect()
    alerts = evaluate(conn, rows, now, load_steam())
    for name in ROWS:
        up, down, total = rows.get(name, (0, 0, 0))
        print(f"{name:<11} up {gb(up):8.1f} GB  down {gb(down):8.1f} GB  total {gb(total):8.1f} GB")
    if args.dry_run:
        for key, subject, _ in alerts:
            print("would alert:", key, "-", subject)
        return
    if args.test_email:
        send("Data usage monitor test", _status_only(rows, now))
        print("test email sent to", config.REPORT_TO)
    for key, subject, body in alerts:
        send(subject, body)
        mark_sent(conn, key, now)
        print("alert sent:", subject)
    record(conn, rows, now)


def _status_only(rows, now):
    cap = config.DATA_CAP_GB
    used = gb(rows["This month"][2])
    return (f"The data usage monitor is working.\n\n"
            f"{now:%B} so far: {used:,.0f} GB of {cap:,.0f} GB ({used / cap * 100:.0f}%)\n"
            f"Last month: {gb(rows['Last month'][2]):,.0f} GB\n\n"
            f"You'll get an email at 50/75/90/100% of the cap, if the month is on pace to go over, "
            f"if one day uses more than {config.DAILY_ALERT_GB:.0f} GB, or if more than "
            f"{config.HOURLY_ALERT_GB:.0f} GB goes through in an hour.")


if __name__ == "__main__":
    main()

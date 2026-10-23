"""Build the monthly outage report and (optionally) email it.

  python3 -m monitor.report                      # previous month, print text
  python3 -m monitor.report --month 2026-10 --out report.html
  python3 -m monitor.report --send               # previous month, email it
"""
import argparse
import csv
import html
import io
import smtplib
import calendar
import statistics
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

from . import config, db

GAP_FACTOR = 3  # a hole of > 3 check intervals means the monitor itself wasn't running


@dataclass
class Event:
    kind: str          # isp_down | dns_down | lan_down | monitor_offline
    start: int
    end: int
    modem_down: bool = False
    note: str = ""

    @property
    def seconds(self):
        return self.end - self.start


def derive_events(samples, interval):
    """Turn per-sample rows (ts, status, modem_ok) into outage / offline events."""
    events, current, prev_ts = [], None, None
    gap_limit = interval * GAP_FACTOR
    for ts, status, modem_ok in samples:
        if prev_ts is not None and ts - prev_ts > gap_limit:
            if current:
                current.end = prev_ts + interval
                current.note = "monitor went offline during this outage"
                events.append(current)
                current = None
            events.append(Event("monitor_offline", prev_ts + interval, ts))
        if status == "ok":
            if current:
                current.end = ts
                events.append(current)
                current = None
        else:
            if current and current.kind != status:
                current.end = ts
                events.append(current)
                current = None
            if not current:
                current = Event(status, ts, ts)
            if not modem_ok:
                current.modem_down = True
        prev_ts = ts
    if current:
        current.end = prev_ts + interval
        current.note = "still ongoing at end of data"
        events.append(current)
    return events


def clip(events, start, end):
    out = []
    for e in events:
        s, t = max(e.start, start), min(e.end, end)
        if t > s:
            out.append(Event(e.kind, s, t, e.modem_down, e.note))
    return out


def month_window(month):
    year, mon = map(int, month.split("-"))
    start = datetime(year, mon, 1)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1)
    return int(start.timestamp()), int(end.timestamp()), start.strftime("%B %Y")


def previous_month():
    now = datetime.now()
    year, mon = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f"{year}-{mon:02d}"


def fmt_time(ts):
    return datetime.fromtimestamp(ts).astimezone().strftime("%a %b %d, %Y %I:%M %p %Z").replace(" 0", " ")


def fmt_dur(seconds):
    minutes = round(seconds / 60)
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def build(conn, month):
    win_start, win_end, label = month_window(month)
    pad = 3600
    rows = conn.execute(
        "SELECT ts, status, modem_ok FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts",
        (win_start - pad, win_end + pad),
    ).fetchall()
    events = clip(derive_events([tuple(r) for r in rows], config.CHECK_INTERVAL), win_start, win_end)

    # Before the first sample (e.g. the month the monitor was installed) counts as not monitored.
    first_ts = rows[0][0] if rows else win_end
    if first_ts > win_start:
        events.insert(0, Event("monitor_offline", win_start, min(first_ts, win_end), note="before monitoring began"))
    now = int(datetime.now().timestamp())
    if win_end > now:
        events.append(Event("monitor_offline", max(now, win_start), win_end, note="future"))

    comcast = [e for e in events if e.kind in ("isp_down", "dns_down")]
    outages = [e for e in comcast if e.seconds >= config.MIN_OUTAGE_SECONDS]
    blips = [e for e in comcast if e.seconds < config.MIN_OUTAGE_SECONDS]
    lan = [e for e in events if e.kind == "lan_down"]
    offline = sum(e.seconds for e in events if e.kind == "monitor_offline")
    monitored = (win_end - win_start) - offline
    down = sum(e.seconds for e in comcast)

    speeds = conn.execute(
        "SELECT ok, download_mbps, upload_mbps FROM speedtests WHERE ts >= ? AND ts < ?",
        (win_start, win_end),
    ).fetchall()
    good = [s for s in speeds if s["ok"]]
    speed = None
    if good:
        dl = [s["download_mbps"] for s in good]
        ul = [s["upload_mbps"] for s in good]
        speed = {
            "count": len(good), "failed": len(speeds) - len(good),
            "dl_median": statistics.median(dl), "dl_min": min(dl),
            "ul_median": statistics.median(ul), "ul_min": min(ul),
            "below_half": sum(1 for d in dl if d < config.PLAN_MBPS / 2),
        }

    # Calendar days touched by an outage: hourly steps through each one, plus its last second.
    days = {datetime.fromtimestamp(t).date() for e in outages for t in range(e.start, e.end, 3600)}
    days |= {datetime.fromtimestamp(e.end - 1).date() for e in outages}
    year, mon = map(int, month.split("-"))
    charge, charge_note = config.charge_for(month)
    daily_rate = charge / calendar.monthrange(year, mon)[1]

    return {
        "month": month, "label": label,
        "monthly_charge": charge, "charge_note": charge_note,
        "days_affected": len(days),
        "daily_rate": daily_rate,
        "credit_estimate": daily_rate * len(days),
        "outages": outages, "blips": blips, "lan": lan,
        "down_seconds": down,
        "outage_seconds": sum(e.seconds for e in outages),
        "monitored_seconds": monitored,
        "coverage_pct": 100 * monitored / (win_end - win_start),
        "uptime_pct": 100 * (1 - down / monitored) if monitored > 0 else None,
        "speed": speed,
    }


def describe(e):
    notes = []
    if e.kind == "dns_down":
        notes.append("DNS lookups failing")
    if e.modem_down:
        notes.append("modem unreachable")
    if e.note:
        notes.append(e.note)
    return "; ".join(notes)


def plan_line(r):
    name = config.PLAN_NAME or "Plan"
    cost = f", ${r['monthly_charge']:.2f}/month" if r["monthly_charge"] else ""
    note = f" ({r['charge_note']})" if r["charge_note"] else ""
    return f"{name}: up to {config.PLAN_MBPS} Mbps{cost}{note}"


def credit_line(r):
    if not (r["monthly_charge"] and r["days_affected"]):
        return ""
    return (f"Days with an outage: {r['days_affected']} -> estimated credit "
            f"${r['credit_estimate']:.2f} at the daily rate of ${r['daily_rate']:.2f}")


def claim_text(r):
    who = config.CUSTOMER_NAME or "the account holder"
    acct = f" (account {config.ACCOUNT_NUMBER})" if config.ACCOUNT_NUMBER else ""
    addr = f" at {config.SERVICE_ADDRESS}" if config.SERVICE_ADDRESS else ""
    plan = f" on {config.PLAN_NAME}" if config.PLAN_NAME else ""
    lines = [
        f"Hello, I'm {who}{acct}{plan}, requesting a service credit for internet outages{addr} "
        f"during {r['label']}. An always-on monitor on my network recorded "
        f"{len(r['outages'])} outage(s) totaling {fmt_dur(r['outage_seconds'])}, during which "
        f"my router was up but the internet was unreachable:",
    ]
    for e in r["outages"]:
        lines.append(f"  - {fmt_time(e.start)} to {fmt_time(e.end)} ({fmt_dur(e.seconds)})")
    days = f"the {r['days_affected']} affected day(s)" if r["days_affected"] else "the affected days"
    lines.append(f"Please apply a credit for {days}. Thank you.")
    return "\n".join(lines)


def to_text(r):
    out = [f"Internet outage report - {r['label']}", ""]
    if config.SERVICE_ADDRESS:
        out.append(f"Service address: {config.SERVICE_ADDRESS}")
    if config.ACCOUNT_NUMBER:
        out.append(f"Account: {config.ACCOUNT_NUMBER}")
    out += [
        plan_line(r),
        f"Comcast outages (>= {config.MIN_OUTAGE_SECONDS // 60} min): {len(r['outages'])}, "
        f"total {fmt_dur(r['outage_seconds'])}",
        f"Brief drops (< {config.MIN_OUTAGE_SECONDS // 60} min): {len(r['blips'])}, "
        f"total {fmt_dur(sum(e.seconds for e in r['blips']))}",
        f"Uptime: {r['uptime_pct']:.3f}% of monitored time" if r["uptime_pct"] is not None else "Uptime: no data",
        f"Monitoring coverage: {r['coverage_pct']:.1f}% of the month",
        f"Home network issues (not Comcast, not counted): {len(r['lan'])}",
    ]
    if credit_line(r):
        out.append(credit_line(r))
    out.append("")
    for i, e in enumerate(r["outages"], 1):
        extra = describe(e)
        out.append(f"{i}. {fmt_time(e.start)} -> {fmt_time(e.end)}  {fmt_dur(e.seconds)}"
                   + (f"  [{extra}]" if extra else ""))
    s = r["speed"]
    if s:
        out += ["", f"Speed tests: {s['count']} ok, {s['failed']} failed. "
                    f"Download median {s['dl_median']:.0f} / min {s['dl_min']:.0f} Mbps; "
                    f"upload median {s['ul_median']:.0f} / min {s['ul_min']:.0f} Mbps; "
                    f"{s['below_half']} test(s) under half the plan speed."]
    if r["outages"]:
        out += ["", "--- Copy/paste for Xfinity ---", claim_text(r)]
    return "\n".join(out)


def to_html(r):
    esc = html.escape
    stat = lambda label, value: (
        f'<td style="padding:10px 16px;border:1px solid #ddd"><div style="font-size:12px;color:#666">'
        f'{esc(label)}</div><div style="font-size:20px;font-weight:600">{esc(value)}</div></td>')
    uptime = f"{r['uptime_pct']:.3f}%" if r["uptime_pct"] is not None else "n/a"
    parts = [
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:720px;color:#222">',
        f"<h2>Internet outage report &ndash; {esc(r['label'])}</h2>",
        "<p>" + (f"{esc(config.SERVICE_ADDRESS)}<br>" if config.SERVICE_ADDRESS else "")
        + (f"Account {esc(config.ACCOUNT_NUMBER)}<br>" if config.ACCOUNT_NUMBER else "")
        + f"{esc(plan_line(r))}</p>",
        '<table style="border-collapse:collapse"><tr>',
        stat("Comcast outages", str(len(r["outages"]))),
        stat("Total downtime", fmt_dur(r["outage_seconds"])),
        stat("Uptime", uptime),
        stat("Coverage", f"{r['coverage_pct']:.1f}%"),
        "</tr></table>",
        (f"<p><b>{esc(credit_line(r))}</b></p>" if credit_line(r) else ""),
        f"<p style='color:#555'>Plus {len(r['blips'])} brief drop(s) under "
        f"{config.MIN_OUTAGE_SECONDS // 60} min ({fmt_dur(sum(e.seconds for e in r['blips']))} total). "
        f"{len(r['lan'])} home-network issue(s) were excluded because the router itself was unreachable.</p>",
    ]
    if r["outages"]:
        parts.append('<table style="border-collapse:collapse;width:100%;font-size:14px">'
                     '<tr style="background:#f3f3f3"><th align="left" style="padding:6px">#</th>'
                     '<th align="left">Start</th><th align="left">End</th>'
                     '<th align="left">Duration</th><th align="left">Notes</th></tr>')
        for i, e in enumerate(r["outages"], 1):
            parts.append(f'<tr style="border-top:1px solid #ddd"><td style="padding:6px">{i}</td>'
                         f"<td>{esc(fmt_time(e.start))}</td><td>{esc(fmt_time(e.end))}</td>"
                         f"<td>{esc(fmt_dur(e.seconds))}</td><td>{esc(describe(e))}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p><b>No Comcast outages recorded this month.</b></p>")
    s = r["speed"]
    if s:
        parts.append(f"<h3>Speed</h3><p>{s['count']} tests ({s['failed']} failed). "
                     f"Download median <b>{s['dl_median']:.0f} Mbps</b> (min {s['dl_min']:.0f}); "
                     f"upload median <b>{s['ul_median']:.0f} Mbps</b> (min {s['ul_min']:.0f}). "
                     f"{s['below_half']} test(s) were under half of the {config.PLAN_MBPS} Mbps plan.</p>")
    if r["outages"]:
        parts.append("<h3>Copy/paste for Xfinity chat</h3>"
                     f'<pre style="white-space:pre-wrap;background:#f6f6f6;padding:12px">{esc(claim_text(r))}</pre>')
    parts.append('<p style="font-size:12px;color:#888">Method: an always-on computer wired to the router pings the '
                 f"router, the cable modem and {len(config.INTERNET_TARGETS)} public servers every "
                 f"{config.CHECK_INTERVAL}s. An outage = router reachable but every public server unreachable "
                 "(confirmed by a retry). Times when the monitor itself was off are not counted.</p></div>")
    return "\n".join(parts)


def to_csv(r):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["start", "end", "duration_minutes", "type", "notes"])
    for e in sorted(r["outages"] + r["blips"], key=lambda e: e.start):
        w.writerow([fmt_time(e.start), fmt_time(e.end), round(e.seconds / 60, 1), e.kind, describe(e)])
    return buf.getvalue()


def send(r):
    if not (config.REPORT_TO and config.SMTP_USER and config.SMTP_APP_PASSWORD):
        raise SystemExit("REPORT_TO / SMTP_USER / SMTP_APP_PASSWORD not configured")
    n = len(r["outages"])
    subject = (f"Internet outage report {r['label']}: {n} outage(s), {fmt_dur(r['outage_seconds'])} down"
               if n else f"Internet outage report {r['label']}: no outages")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, config.SMTP_USER, config.REPORT_TO
    msg.set_content(to_text(r))
    msg.add_alternative(to_html(r), subtype="html")
    msg.add_attachment(to_csv(r).encode(), maintype="text", subtype="csv",
                       filename=f"outages-{r['month']}.csv")
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.login(config.SMTP_USER, config.SMTP_APP_PASSWORD)
        smtp.send_message(msg)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--month", default=previous_month(), help="YYYY-MM (default: previous month)")
    p.add_argument("--out", help="write the HTML report to this file")
    p.add_argument("--send", action="store_true", help="email the report")
    args = p.parse_args()

    r = build(db.connect(), args.month)
    print(to_text(r))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(to_html(r))
    if args.send:
        send(r)
        print(f"\nSent to {config.REPORT_TO}")


if __name__ == "__main__":
    main()

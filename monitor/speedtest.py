"""Run one Ookla speedtest and store the result. Triggered by a systemd timer."""
import json
import subprocess
import time

from . import db


def main():
    ts = int(time.time())
    conn = db.connect()
    try:
        out = subprocess.run(
            ["speedtest", "--accept-license", "--accept-gdpr", "-f", "json"],
            capture_output=True, text=True, timeout=180, check=True,
        ).stdout
        data = json.loads(out)
        row = (
            ts, 1,
            data["download"]["bandwidth"] * 8 / 1e6,
            data["upload"]["bandwidth"] * 8 / 1e6,
            data["ping"]["latency"],
            f'{data["server"]["name"]} ({data["server"]["location"]})',
            data.get("result", {}).get("url"),
            None,
        )
    except Exception as exc:  # outage, timeout, bad JSON - record the failure
        row = (ts, 0, None, None, None, None, None, str(exc)[:500])
    conn.execute("INSERT INTO speedtests VALUES (?,?,?,?,?,?,?,?)", row)
    conn.commit()
    print(row)


if __name__ == "__main__":
    main()

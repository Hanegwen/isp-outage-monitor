import unittest
from datetime import datetime
from unittest import mock

from monitor import config, db, usage


def cell(*vals):
    return "<td>" + "".join(f"<p align=center>{v}</p>" for v in vals) + "</td>"


def page(month_total_mb, yesterday_mb=23000):
    rows = [("Today", ("100.5",), ("2500",), ("2600.5",)),
            ("Yesterday", ("3000",), ("20000",), (str(yesterday_mb),)),
            ("This week", ("10000 / ", "1500"), ("80000 / ", "11000"), ("90000 / ", "12500")),
            ("This month", ("18000 / ", "600.5"), ("214000 / ", "7100"), (f"{month_total_mb} / ", "7700")),
            ("Last month", ("50000 / ", "1667"), ("650000 / ", "21667"), ("700000 / ", "23333"))]
    html = "<table>"
    for name, up, down, total in rows:
        html += f'<tr><td><span class="thead">{name}</span></td><td><span class=ttext1 disabled>--:--</span></td>'
        html += cell(*up) + cell(*down) + cell(*total) + "</tr>\n"
    return html + "</table>"


class Parse(unittest.TestCase):
    def test_reads_all_rows_taking_totals_not_averages(self):
        rows = usage.parse(page(232000))
        self.assertEqual(rows["Today"], (100.5, 2500.0, 2600.5))
        self.assertEqual(rows["This month"], (18000.0, 214000.0, 232000.0))
        self.assertEqual(rows["Last month"][2], 700000.0)


class Evaluate(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        config.DATA_CAP_GB, config.DAILY_ALERT_GB, config.HOURLY_ALERT_GB = 1200, 60, 15

    def keys(self, month_mb, now, **kw):
        return [k for k, _, _ in usage.evaluate(self.conn, usage.parse(page(month_mb, **kw)), now)]

    def test_quiet_month_no_alerts(self):
        self.assertEqual(self.keys(232000, datetime(2026, 10, 14, 7)), [])

    def test_highest_cap_level_only_then_not_repeated(self):
        now = datetime(2026, 10, 28, 12)
        self.assertEqual(self.keys(950_000, now), ["cap75:2026-10"])
        usage.mark_sent(self.conn, "cap75:2026-10", now)
        self.assertEqual(self.keys(950_000, now), [])
        self.assertEqual(self.keys(1_090_000, now), ["cap90:2026-10", "pace:2026-10"])

    def test_pace_alert_waits_until_day_5(self):
        self.assertEqual(self.keys(250_000, datetime(2026, 10, 4, 12)), [])
        self.assertIn("pace:2026-10", self.keys(250_000, datetime(2026, 10, 6, 12)))

    def test_big_day(self):
        self.assertEqual(self.keys(232000, datetime(2026, 10, 14, 7), yesterday_mb=75000), ["daily:2026-10-13"])

    def test_hourly_spike_against_previous_reading(self):
        now = datetime(2026, 10, 14, 7)
        usage.record(self.conn, usage.parse(page(200_000)), datetime(2026, 10, 14, 6))
        self.assertEqual(self.keys(232000, now), ["hourly:2026-10-14"])

    def steam(self, start, gb_total, downloading=False, games=("Example Game",)):
        t0 = start.timestamp()
        events = [[int(t0 + 330 * (i + 1)), int(gb_total * 1e9 / 10)] for i in range(10)]
        return {"generated": events[-1][0], "events": events, "downloading": downloading,
                "apps": [[int(t0) + 60, g] for g in games]}

    def test_steam_update_gets_a_calm_note_instead_of_the_warning(self):
        prev, now = datetime(2026, 10, 23, 13), datetime(2026, 10, 23, 14)
        usage.record(self.conn, usage.parse(page(410_000)), prev)
        alerts = usage.evaluate(self.conn, usage.parse(page(436_000)), now, self.steam(prev, 21))
        self.assertEqual([k for k, _, _ in alerts], ["steam:2026-10-23"])
        self.assertIn("Example Game", alerts[0][1])

    def test_spike_steam_does_not_explain_still_warns(self):
        prev, now = datetime(2026, 10, 23, 13), datetime(2026, 10, 23, 14)
        usage.record(self.conn, usage.parse(page(410_000)), prev)
        alerts = usage.evaluate(self.conn, usage.parse(page(450_000)), now, self.steam(prev, 5))
        self.assertEqual([k for k, _, _ in alerts], ["hourly:2026-10-23"])
        self.assertIn("Steam on the PC accounts for 5 GB", alerts[0][2])

    def test_no_steam_report_warns_as_before(self):
        prev, now = datetime(2026, 10, 23, 13), datetime(2026, 10, 23, 14)
        usage.record(self.conn, usage.parse(page(410_000)), prev)
        self.assertEqual([k for k, _, _ in usage.evaluate(self.conn, usage.parse(page(436_000)), now, None)],
                         ["hourly:2026-10-23"])

    def test_running_download_fills_in_the_minutes_not_yet_reported(self):
        start = datetime(2026, 10, 23, 13)
        steam = self.steam(start, 10, downloading=True)
        last = steam["events"][-1][0]
        gb_seen, _ = usage.steam_between(steam, start.timestamp(), last)
        gb_now, _ = usage.steam_between(steam, start.timestamp(), last + 300, last + 300)
        self.assertAlmostEqual(gb_seen, 10)
        self.assertGreater(gb_now, gb_seen)

    def test_big_day_from_steam_is_a_calm_note(self):
        now = datetime(2026, 10, 24, 1)
        alerts = usage.evaluate(self.conn, usage.parse(page(232000, yesterday_mb=75000)), now,
                                self.steam(datetime(2026, 10, 23, 13), 40))
        self.assertEqual([k for k, _, _ in alerts], ["daily:2026-10-23"])
        self.assertIn("Steam game updates", alerts[0][1])

    def test_counter_recovering_from_a_low_glitch_is_not_a_spike(self):
        # the R7000 has done this: the month total read 300 GB, then ~100 GB for five hours, then 310 GB again
        usage.record(self.conn, usage.parse(page(300_000)), datetime(2026, 10, 18, 19))
        for hour in (20, 21, 22, 23):
            usage.record(self.conn, usage.parse(page(100_000)), datetime(2026, 10, 18, hour))
        usage.record(self.conn, usage.parse(page(100_500)), datetime(2026, 10, 19, 0))
        self.assertEqual(self.keys(310_000, datetime(2026, 10, 19, 1)), [])


class Main(unittest.TestCase):
    def test_no_router_password_exits_cleanly(self):
        saved = config.ROUTER_PASSWORD
        try:
            config.ROUTER_PASSWORD = ""
            with mock.patch("sys.argv", ["usage"]), mock.patch.object(usage, "fetch") as fetch, \
                    mock.patch("builtins.print"):
                usage.main()
            fetch.assert_not_called()
        finally:
            config.ROUTER_PASSWORD = saved


if __name__ == "__main__":
    unittest.main()

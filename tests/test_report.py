import unittest
from datetime import datetime

from monitor import db, report


def samples(start, statuses, interval=30):
    return [(start + i * interval, s, 1) for i, s in enumerate(statuses)]


class DeriveEvents(unittest.TestCase):
    def test_outage_runs_from_first_failure_to_first_recovery(self):
        rows = samples(1000, ["ok", "isp_down", "isp_down", "ok"])
        [e] = report.derive_events(rows, 30)
        self.assertEqual((e.kind, e.start, e.end), ("isp_down", 1030, 1090))

    def test_gap_becomes_monitor_offline_and_cuts_outage(self):
        rows = [(1000, "isp_down", 1), (1030, "isp_down", 0), (5000, "ok", 1)]
        outage, offline = report.derive_events(rows, 30)
        self.assertEqual((outage.kind, outage.start, outage.end), ("isp_down", 1000, 1060))
        self.assertTrue(outage.modem_down)
        self.assertEqual((offline.kind, offline.start, offline.end), ("monitor_offline", 1060, 5000))

    def test_kind_change_splits_events(self):
        rows = samples(0, ["isp_down", "lan_down", "ok"])
        kinds = [e.kind for e in report.derive_events(rows, 30)]
        self.assertEqual(kinds, ["isp_down", "lan_down"])


class Build(unittest.TestCase):
    def test_month_summary(self):
        conn = db.connect(":memory:")
        start = int(datetime(2026, 9, 1).timestamp())
        end = int(datetime(2026, 10, 1).timestamp())
        statuses = {}
        # 10-minute outage on day 5, 1-minute blip on day 6, router down on day 7
        for t in range(start + 5 * 86400, start + 5 * 86400 + 600, 30):
            statuses[t] = "isp_down"
        for t in range(start + 6 * 86400, start + 6 * 86400 + 60, 30):
            statuses[t] = "isp_down"
        for t in range(start + 7 * 86400, start + 7 * 86400 + 300, 30):
            statuses[t] = "lan_down"
        conn.executemany("INSERT INTO samples VALUES (?,1,1,1,1,?)",
                         [(t, statuses.get(t, "ok")) for t in range(start, end, 30)])
        r = report.build(conn, "2026-09")
        self.assertEqual(len(r["outages"]), 1)
        self.assertEqual(r["outages"][0].seconds, 600)
        self.assertEqual(len(r["blips"]), 1)
        self.assertEqual(len(r["lan"]), 1)
        self.assertGreater(r["coverage_pct"], 99.9)
        self.assertIn("10 min", report.to_text(r))
        self.assertEqual(r["days_affected"], 1)
        self.assertAlmostEqual(r["daily_rate"] * 30, report.config.MONTHLY_CHARGE)
        report.to_html(r)
        report.to_csv(r)

    def test_promo_charge_only_inside_its_months(self):
        cfg = report.config
        saved = (cfg.MONTHLY_CHARGE, cfg.PROMO_CHARGE, cfg.PROMO_FROM, cfg.PROMO_UNTIL, cfg.PROMO_NOTE)
        try:
            cfg.MONTHLY_CHARGE, cfg.PROMO_CHARGE = 70.0, 50.0
            cfg.PROMO_FROM, cfg.PROMO_UNTIL, cfg.PROMO_NOTE = "2026-11", "2027-10", "promo price"
            self.assertEqual(cfg.charge_for("2026-10"), (70.0, ""))
            self.assertEqual(cfg.charge_for("2026-11"), (50.0, "promo price"))
            self.assertEqual(cfg.charge_for("2027-10"), (50.0, "promo price"))
            self.assertEqual(cfg.charge_for("2027-11"), (70.0, ""))
            r = report.build(db.connect(":memory:"), "2027-01")
            self.assertIn("$50.00/month (promo price)", report.to_text(r))
        finally:
            (cfg.MONTHLY_CHARGE, cfg.PROMO_CHARGE, cfg.PROMO_FROM, cfg.PROMO_UNTIL, cfg.PROMO_NOTE) = saved

    def test_html_skips_empty_address(self):
        saved = report.config.SERVICE_ADDRESS
        try:
            report.config.SERVICE_ADDRESS = ""
            self.assertNotIn("<p><br>", report.to_html(report.build(db.connect(":memory:"), "2026-09")))
            report.config.SERVICE_ADDRESS = "123 Main St"
            self.assertIn("<p>123 Main St<br>", report.to_html(report.build(db.connect(":memory:"), "2026-09")))
        finally:
            report.config.SERVICE_ADDRESS = saved


if __name__ == "__main__":
    unittest.main()

"""Settings come from environment variables (installed: /etc/comcast-monitor.env)."""
import os


def _get(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


DB_PATH = _get("DB_PATH", "/var/lib/comcast-monitor/monitor.db")

# Checker
CHECK_INTERVAL = int(_get("CHECK_INTERVAL", "30"))  # seconds between samples
GATEWAY_IP = _get("GATEWAY_IP", "192.168.1.1")      # your router
MODEM_IP = _get("MODEM_IP", "192.168.100.1")        # cable modem status page (most DOCSIS modems)
INTERNET_TARGETS = _get("INTERNET_TARGETS", "1.1.1.1,8.8.8.8,9.9.9.9").split(",")
DNS_TEST_HOST = _get("DNS_TEST_HOST", "www.google.com")

# Report
MIN_OUTAGE_SECONDS = int(_get("MIN_OUTAGE_SECONDS", "120"))
PLAN_MBPS = int(_get("PLAN_MBPS", "800"))
PLAN_NAME = _get("PLAN_NAME", "")
MONTHLY_CHARGE = float(_get("MONTHLY_CHARGE", "0"))  # $/month, used for the credit estimate
# Temporary price (e.g. a 12-month loyalty discount): used for report months PROMO_FROM..PROMO_UNTIL (YYYY-MM, inclusive)
PROMO_CHARGE = float(_get("PROMO_CHARGE", "0"))
PROMO_FROM = _get("PROMO_FROM", "")
PROMO_UNTIL = _get("PROMO_UNTIL", "")
PROMO_NOTE = _get("PROMO_NOTE", "")
SERVICE_ADDRESS = _get("SERVICE_ADDRESS", "")
ACCOUNT_NUMBER = _get("ACCOUNT_NUMBER", "")
CUSTOMER_NAME = _get("CUSTOMER_NAME", "")

# Data usage (router Traffic Meter)
ROUTER_USER = _get("ROUTER_USER", "admin")
ROUTER_PASSWORD = _get("ROUTER_PASSWORD", "")
DATA_CAP_GB = float(_get("DATA_CAP_GB", "1200"))      # Xfinity monthly allowance
DAILY_ALERT_GB = float(_get("DAILY_ALERT_GB", "60"))  # alert when one day uses more than this
HOURLY_ALERT_GB = float(_get("HOURLY_ALERT_GB", "15"))  # alert when an hour uses more than this
STEAM_REPORT = _get("STEAM_REPORT", "/var/lib/comcast-monitor/steam.json")  # written by pc/steam-report.ps1

# Email (Gmail SMTP with an app password)
REPORT_TO = _get("REPORT_TO", "")
SMTP_HOST = _get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_get("SMTP_PORT", "465"))
SMTP_USER = _get("SMTP_USER", "")
SMTP_APP_PASSWORD = _get("SMTP_APP_PASSWORD", "")


def charge_for(month):
    """Monthly charge for a report month (YYYY-MM) and a note when the temporary price applies."""
    if PROMO_CHARGE and PROMO_FROM <= month <= PROMO_UNTIL:
        return PROMO_CHARGE, PROMO_NOTE or f"temporary price until {PROMO_UNTIL}"
    return MONTHLY_CHARGE, ""

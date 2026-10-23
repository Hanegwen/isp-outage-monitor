"""Long-running connectivity checker. Writes one sample every CHECK_INTERVAL seconds.

Status per sample:
  ok        - internet reachable and DNS resolves
  isp_down  - router answers but no internet target does (Comcast side)
  dns_down  - internet IPs answer but name lookups fail
  lan_down  - the router itself doesn't answer (home network problem, not Comcast)
"""
import logging
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, db

log = logging.getLogger("checker")


def ping(host, timeout=2):
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout), host],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def dns_ok(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def probe(pool):
    hosts = [config.GATEWAY_IP, config.MODEM_IP, *config.INTERNET_TARGETS]
    results = dict(zip(hosts, pool.map(ping, hosts)))
    return (
        results[config.GATEWAY_IP],
        results[config.MODEM_IP],
        any(results[h] for h in config.INTERNET_TARGETS),
        dns_ok(config.DNS_TEST_HOST),
    )


def classify(gateway, internet, dns):
    if not gateway:
        return "lan_down"
    if not internet:
        return "isp_down"
    if not dns:
        return "dns_down"
    return "ok"


def take_sample(pool):
    gateway, modem, internet, dns = probe(pool)
    status = classify(gateway, internet, dns)
    if status != "ok":
        # Retry once so a single dropped packet isn't logged as an outage.
        time.sleep(3)
        gateway, modem, internet, dns = probe(pool)
        status = classify(gateway, internet, dns)
    return gateway, modem, internet, dns, status


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = db.connect()
    last_status = None
    with ThreadPoolExecutor(max_workers=8) as pool:
        while True:
            started = time.time()
            gateway, modem, internet, dns, status = take_sample(pool)
            conn.execute(
                "INSERT INTO samples VALUES (?,?,?,?,?,?)",
                (int(started), int(gateway), int(modem), int(internet), int(dns), status),
            )
            conn.commit()
            if status != last_status:
                log.info("status %s -> %s (gateway=%s modem=%s internet=%s dns=%s)",
                         last_status, status, gateway, modem, internet, dns)
                last_status = status
            time.sleep(max(1, config.CHECK_INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()

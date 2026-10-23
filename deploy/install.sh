#!/bin/sh
# Run as root after the code is copied to /opt/comcast-monitor:
#   sudo ./deploy/install.sh                        # services run as the system user comcast-monitor
#   sudo SERVICE_USER=pi ./deploy/install.sh        # or as an existing user
# Updating an existing install keeps the user its services already run as, unless SERVICE_USER is given.
set -e
cd /opt/comcast-monitor
current=$(sed -n 's/^User=//p' /etc/systemd/system/comcast-monitor.service 2>/dev/null | head -n 1)
SERVICE_USER=${SERVICE_USER:-${current:-comcast-monitor}}
echo "Services will run as: $SERVICE_USER"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    if [ "$SERVICE_USER" != comcast-monitor ]; then
        echo "User $SERVICE_USER does not exist." >&2
        exit 1
    fi
    # Home = the state dir (created by systemd) so the Ookla CLI can save its license acceptance.
    useradd --system --no-create-home --home-dir /var/lib/comcast-monitor --shell /usr/sbin/nologin "$SERVICE_USER"
fi
SERVICE_GROUP=$(id -gn "$SERVICE_USER")

if ! command -v speedtest >/dev/null; then
    case $(uname -m) in
        aarch64) arch=aarch64 ;;
        armv7l) arch=armhf ;;
        x86_64) arch=x86_64 ;;
        *) echo "No Ookla speedtest build known for $(uname -m); install it by hand: https://www.speedtest.net/apps/cli" >&2
           exit 1 ;;
    esac
    tmp=$(mktemp -d)
    curl -fsSL "https://install.speedtest.net/app/cli/ookla-speedtest-1.2.0-linux-$arch.tgz" | tar -xz -C "$tmp"
    install -m 755 "$tmp/speedtest" /usr/local/bin/speedtest
    rm -rf "$tmp"
fi

if [ ! -f /etc/comcast-monitor.env ]; then
    install -m 640 -o root -g "$SERVICE_GROUP" config.example.env /etc/comcast-monitor.env
    echo "Created /etc/comcast-monitor.env - fill in the email settings."
else
    chown root:"$SERVICE_GROUP" /etc/comcast-monitor.env
    chmod 640 /etc/comcast-monitor.env
fi

sed "s/@SERVICE_USER@/$SERVICE_USER/g" deploy/cm > /usr/local/bin/cm
chmod 755 /usr/local/bin/cm
for f in deploy/*.service deploy/*.timer; do
    sed "s/@SERVICE_USER@/$SERVICE_USER/g" "$f" > "/etc/systemd/system/${f#deploy/}"
done
systemctl daemon-reload
systemctl enable comcast-monitor.service comcast-speedtest.timer comcast-report.timer comcast-usage.timer
systemctl restart comcast-monitor.service
systemctl start comcast-speedtest.timer comcast-report.timer comcast-usage.timer
systemctl --no-pager --lines=0 status comcast-monitor.service

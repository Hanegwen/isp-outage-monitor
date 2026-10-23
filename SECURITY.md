# Security

## Keep your secrets out of git

Your real settings live in `/etc/comcast-monitor.env` on the machine that runs the monitor. It holds your
Gmail app password, router password and account details. **Never commit it**, paste it into an issue, or
copy it back into `config.example.env`. `.gitignore` ignores `*.env` (except the example) and local
databases, but check `git status` before you push a fork.

If you leak a password anyway: revoke the Gmail app password (Google Account > Security > App passwords),
change the router password, then remove the file from your repo history.

## Reporting a problem

- Security issue: open a private report under **Security > Report a vulnerability** on the GitHub repo
  (private advisory). Please don't put details in a public issue.
- Anything else: open a normal GitHub issue.

## Threat notes

- The data-usage feature logs in to the router with HTTP basic auth over **plain HTTP** on your LAN
  (that's how Netgear's admin page works). Anyone who can sniff your LAN could see that password. Use a
  router password you use nowhere else, keep the router's remote management off, or leave
  `ROUTER_PASSWORD` blank to disable the feature.
- `/etc/comcast-monitor.env` should be `640`, owned by root with the service user's group. `install.sh`
  sets this.
- Services run as an unprivileged user (`comcast-monitor` by default), not root.
- All data stays in a local SQLite file. The only outbound traffic is pings to public DNS servers, a DNS
  lookup, the Ookla speed test and Gmail SMTP (TLS).
- The monthly email contains your name, service address and account number if you set them. Send it only
  to addresses you trust.

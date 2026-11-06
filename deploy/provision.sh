#!/usr/bin/env bash
# Provision Where MPs Stand on a fresh Ubuntu 24.04 server (run as root).
#
#   curl -fsSL https://raw.githubusercontent.com/rj-mitchell/wherempsstand/main/deploy/provision.sh | bash
#   (or copy this file to the server and run: sudo bash provision.sh)
#
# What it does:
#   1. installs Docker and git, turns on automatic security updates, a firewall, and
#      fail2ban (blocks addresses that keep failing to log in, while password logins are on)
#   2. clones the repository to /opt/hansardgpt
#   3. asks for your domain and Anthropic API key and writes deploy/.env
#   4. builds and starts the app behind Caddy (automatic HTTPS)
#   5. starts the one-off bootstrap in the background (downloads ~5 GB of Hansard
#      since 2010, ingests, indexes; embeddings continue in later syncs)
#   6. installs the twice-daily sync timer, the nightly backup timer, the watchdog (restarts any site on the server
#      that stops answering), and the hourly disk check and monthly disk report (all three email the alerts address
#      once mail.sh is set up)
# The site is public. An admin token (for /api/admin/usage and /api/admin/sync) is
# generated into deploy/.env. For an invite-only site instead, see docs/OPERATIONS.md.
set -euo pipefail

REPO="${REPO:-https://github.com/rj-mitchell/wherempsstand.git}"
BRANCH="${BRANCH:-main}"
DIR=/opt/hansardgpt

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

echo "==> Capping the size of every container's log"
# Docker's default for containers started outside the sites' compose files (which set the same limits themselves).
# Written before Docker is installed, so it takes effect from the start. On a server that already runs Docker, it
# takes effect only when Docker restarts, which stops every site for a few seconds: docs/HOSTING.md, "Disk space".
mkdir -p /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
  printf '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}\n' > /etc/docker/daemon.json
  if command -v docker >/dev/null; then
    echo "    Docker is already running: the log cap applies when it next restarts (docs/HOSTING.md, \"Disk space\")."
  fi
elif ! grep -q '"max-size"' /etc/docker/daemon.json; then
  echo "    /etc/docker/daemon.json exists without a log cap; left alone (docs/HOSTING.md, \"Disk space\")."
fi

echo "==> Installing Docker and git"
apt-get update -y
apt-get install -y ca-certificates curl git openssl
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

echo "==> Turning on automatic security updates, the firewall (SSH, HTTP and HTTPS only) and fail2ban"
apt-get install -y unattended-upgrades ufw fail2ban python3-systemd
# read SSH logins from the system journal (some server images have no /var/log/auth.log)
printf '[sshd]\nenabled = true\nbackend = systemd\n' > /etc/fail2ban/jail.d/where-they-stand.conf
systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban || echo "WARNING: fail2ban did not start; carrying on (deploy/check.sh will flag it)."
dpkg-reconfigure -f noninteractive unattended-upgrades
# a file read last, so no image default can switch security updates off again; and the timers that act on it
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/99zz-auto-upgrades
systemctl unmask apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
# (sshd -T can fail on Ubuntu 24.04 when ssh is socket-activated; then the default port 22 is used)
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}' || true)"
ufw allow "${SSH_PORT:-22}/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Fetching Where MPs Stand into $DIR"
if [[ -d $DIR/.git ]]; then
  git -C "$DIR" pull --ff-only
else
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR/deploy"

if [[ ! -f .env ]]; then
  read -rp "Main domain name pointing at this server (e.g. wherempsstand.com): " DOMAIN
  read -rp "Other domain names that should redirect to it, space-separated (www. is added to each, and to the main one), or blank: " OTHERS
  REDIRECTS="www.$DOMAIN"
  for d in $OTHERS; do REDIRECTS="$REDIRECTS, $d, www.$d"; done
  read -rsp "Anthropic API key (leave blank for sources-only mode): " KEY
  echo
  umask 077
  printf 'DOMAIN=%s\nREDIRECT_DOMAINS="%s"\nHANSARD_ANTHROPIC_API_KEY=%s\nHANSARD_ADMIN_TOKEN=%s\n' \
    "$DOMAIN" "$REDIRECTS" "$KEY" "$(openssl rand -hex 24)" > .env
fi

echo "==> Adding swap space (spare memory on disk for large updates)"
bash "$DIR/deploy/swap.sh" || echo "WARNING: could not add swap; carrying on."

echo "==> Keeping the system log and apt's downloads in check"
bash "$DIR/deploy/housekeeping.sh" || echo "WARNING: could not apply the log and download limits; carrying on."

echo "==> Writing the web server's own settings (Cloudflare's addresses, the certificate)"
bash "$DIR/deploy/caddy-local.sh"
docker network create sites >/dev/null 2>&1 || true  # shared with the other sites on this server

echo "==> Building and starting"
docker compose up -d --build

echo "==> Starting the initial data load in the background (log: /var/log/hansard-bootstrap.log)"
nohup docker compose run --rm -T app hansard bootstrap --embed-minutes 120 > /var/log/hansard-bootstrap.log 2>&1 &

echo "==> Installing the sync, backup, watchdog and disk timers"
mkdir -p backups
cp hansard-sync.service hansard-sync.timer hansard-backup.service hansard-backup.timer alert@.service \
   wheretheystand-watchdog.service wheretheystand-watchdog.timer \
   wheretheystand-disk.service wheretheystand-disk.timer \
   wheretheystand-disk-report.service wheretheystand-disk-report.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hansard-sync.timer hansard-backup.timer wheretheystand-watchdog.timer \
   wheretheystand-disk.timer wheretheystand-disk-report.timer

cat <<MSG

Where MPs Stand is starting at https://$(grep ^DOMAIN= .env | cut -d= -f2)/
The first data load takes roughly 30-60 minutes (follow /var/log/hansard-bootstrap.log).
Semantic search improves as embeddings complete over the next syncs.

The site is open to everyone. Usage and spend:
  curl -H "Authorization: Bearer \$(grep ^HANSARD_ADMIN_TOKEN= $DIR/deploy/.env | cut -d= -f2)" \\
    https://$(grep ^DOMAIN= .env | cut -d= -f2)/api/admin/usage
MSG

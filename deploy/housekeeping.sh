#!/usr/bin/env bash
# Keep the server's own logs and download caches from filling the disk, for every site on it (run as root; safe to
# repeat). Run by provision.sh and update.sh:
#   * the system log (journald) is capped at 500 MB; it is restarted only when that setting changes;
#   * apt clears out downloaded packages that are no longer available, once a week.
# Docker's own default log size is set by provision.sh only, because applying it means restarting Docker (and every
# site with it): docs/HOSTING.md, "Disk space".
set -euo pipefail

journald_conf=/etc/systemd/journald.conf.d/90-wheretheystand.conf
want=$'# Written by /opt/hansardgpt/deploy/housekeeping.sh: keep the system log under 500 MB.\n[Journal]\nSystemMaxUse=500M\n'
if [[ "$(cat "$journald_conf" 2>/dev/null)"$'\n' != "$want" ]]; then
  mkdir -p "$(dirname "$journald_conf")"
  printf '%s' "$want" > "$journald_conf"
  systemctl restart systemd-journald
  journalctl --vacuum-size=500M >/dev/null 2>&1 || true
  echo "housekeeping: the system log is now capped at 500 MB"
fi

apt_conf=/etc/apt/apt.conf.d/99zz-wheretheystand-autoclean
want=$'// Written by /opt/hansardgpt/deploy/housekeeping.sh: once a week, delete downloaded packages that are out of date.\nAPT::Periodic::AutocleanInterval "7";\n'
if [[ "$(cat "$apt_conf" 2>/dev/null)"$'\n' != "$want" ]]; then
  printf '%s' "$want" > "$apt_conf"
  echo "housekeeping: apt now clears out old downloaded packages weekly"
fi

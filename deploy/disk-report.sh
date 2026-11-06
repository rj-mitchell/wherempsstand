#!/usr/bin/env bash
# Email the alerts address a monthly report on where the server's disk is going, for every site on this server.
# Run on the 1st of each month by wheretheystand-disk-report.timer; run it by hand at any time with
#   bash /opt/hansardgpt/deploy/disk-report.sh
# Changes nothing except the note of this month's figure, which next month's report compares against.
set -uo pipefail
DIR="${DIR:-/opt/hansardgpt}"
STATE="${MAIL_STATE:-/var/lib/wheretheystand}"
LAST="$STATE/disk-last.txt"
mkdir -p "$STATE"
used="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
report="$(mktemp)"
trap 'rm -f "$report"' EXIT

mountpoint() { docker volume inspect -f '{{.Mountpoint}}' "$1" 2>/dev/null; }

{
  echo "Disk on $(hostname), $(date '+%-d %B %Y')"
  echo
  df -h / | tail -1
  [[ -f "$LAST" ]] && echo "A month ago: $(cat "$LAST")"
  echo
  echo "== Memory and swap"
  free -h
  swapon --show 2>/dev/null
  echo
  echo "== Docker (images, containers, data volumes, build cache)"
  timeout 120 docker system df 2>/dev/null
  echo
  echo "== Each site's data volume, with its largest folders"
  for v in $(docker volume ls -q 2>/dev/null); do
    m="$(mountpoint "$v")"
    [[ -n "$m" && -d "$m" ]] || continue
    echo "-- $v"
    timeout 900 du -h --max-depth=2 "$m" 2>/dev/null | sort -h | tail -9 | sed "s|$m|.|"
  done
  echo
  echo "== Search indexes (size, and how many versions each keeps)"
  find /var/lib/docker/volumes/*/_data -maxdepth 4 -type d -name '*.lance' 2>/dev/null | while read -r t; do
    printf '%8s %6s versions  %s\n' "$(du -sh "$t" 2>/dev/null | cut -f1)" \
      "$(find "$t/_versions" -maxdepth 1 -type f 2>/dev/null | wc -l)" "${t#/var/lib/docker/volumes/}"
  done | sort -h | tail -15
  echo
  echo "== Each site's folder under /opt (code and local backups)"
  du -sh /opt/* 2>/dev/null | sort -h
  echo
  echo "== Logs"
  journalctl --disk-usage 2>/dev/null
  du -sh /var/log 2>/dev/null
  echo
  echo "What to do if it is filling: docs/HOSTING.md in the Where MPs Stand repository, 'Disk space'."
} > "$report"

cat "$report"   # also into the system log: journalctl -u wheretheystand-disk-report
bash "$DIR/deploy/mail.sh" alert "Monthly disk report: ${used}% used" < "$report"
printf '%s%% used (%s) on %s\n' "$used" "$(df -h --output=used / | tail -1 | tr -d ' ')" "$(date '+%-d %B %Y')" > "$LAST"

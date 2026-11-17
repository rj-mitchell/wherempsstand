#!/usr/bin/env bash
# Email the alerts address when the server's disk is 80% full or more. Run every hour by wheretheystand-disk.timer,
# for every site on this server. alert.sh sends the same alert at most once every six hours, so a disk that stays
# full doesn't flood the inbox. Changes nothing. What to do about a full disk: docs/HOSTING.md, "Disk space".
set -uo pipefail
DIR="${DIR:-/opt/hansardgpt}"
limit="${DISK_ALERT_PCT:-80}"
used="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
if (( ${used:-0} < limit )); then
  exit 0
fi
echo "The disk is ${used}% full (alert at ${limit}%): emailing the alerts address"
bash "$DIR/deploy/alert.sh" wheretheystand-disk.service "The server's disk is ${used}% full (the alert is at ${limit}%).

$(df -h / | tail -1)

Docker's share:
$(timeout 120 docker system df 2>/dev/null)

The largest folders:
$(timeout 300 du -xsh /var/lib/docker/volumes/*/_data /var/lib/docker/overlay2 /var/lib/docker/containerd \
    /opt/* /var/log /var/cache/apt /swapfile 2>/dev/null | sort -h | tail -12)

What to do: docs/HOSTING.md in the Where MPs Stand repository, 'Disk space'."

#!/usr/bin/env bash
# Email the alerts address when a scheduled job on this server fails: every site's update and backup units name
# alert@%n.service in OnFailure=, which runs this with the failed unit's name. The same unit is reported at most once
# every six hours, so a job failing on each run doesn't flood the inbox. Also run by watchdog.sh about a site that has
# stopped answering, with ALERT_KEY set so that each site (and each kind of alert) counts on its own for those six
# hours. Sending needs mail.sh set up (docs/HOSTING.md, "Email from the server"); until then alerts go only to the log.
set -uo pipefail
unit="${1:?usage: alert.sh UNIT [MESSAGE]}"
note="${2:-}"
DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="${MAIL_STATE:-/var/lib/wheretheystand}/alerts"
mkdir -p "$STATE"
key="${ALERT_KEY:-$unit}"
stamp="$STATE/${key//\//_}"
if [[ -f "$stamp" && $(( $(date +%s) - $(stat -c %Y "$stamp") )) -lt 21600 ]]; then
  logger -t wheretheystand-alert "$key: already reported in the last six hours"
  exit 0
fi
touch "$stamp"

{
  echo "On $(hostname), at $(date '+%Y-%m-%d %H:%M %Z'):"
  echo
  if [[ -n "$note" ]]; then
    echo "$note"
  else
    echo "$unit did not finish properly."
    echo
    echo "What it says it is: $(systemctl show "$unit" -p Description --value 2>/dev/null)"
    echo "Result: $(systemctl show "$unit" -p Result --value 2>/dev/null), exit status $(systemctl show "$unit" -p ExecMainStatus --value 2>/dev/null)"
  fi
  echo
  echo "Its last lines in the system log (journalctl -u $unit):"
  echo
  journalctl -u "$unit" -n 60 --no-pager -o short-iso 2>/dev/null | cut -c1-400
  echo
  echo "To look on the server: systemctl status $unit --no-pager; journalctl -u $unit -n 200"
} | bash "$DIR/mail.sh" alert "Server alert: ${unit%.service}"

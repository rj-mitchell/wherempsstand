#!/usr/bin/env bash
# Email the alerts address as soon as a reader's report, a statement under the right of reply or a request from
# someone named arrives on any site on this server, rather than only when the nightly family check finds one has
# waited four working days. Run every 15 minutes by wheretheystand-waiting.timer, for Where MPs Stand and each site
# in SITES in deploy/.env.
#
# It asks each site about itself as the nightly check does (GET /api/check with the family's check token, from the
# site's own .env), and so it learns only counts and ages: never a name, a question or a word of a report. When a
# site has more reports or more statements waiting than it had at the last look, it emails the counts and the
# command that shows them. A site with no check token, or that doesn't answer, is skipped (the watchdog looks after
# a site that has stopped). Changes nothing on any site.
#
# What it remembers: the last counts for each site, in $STATE (one small file a site). On the first look at a site
# anything already waiting is emailed once, so that nothing waiting is missed.
set -uo pipefail
DIR="${DIR:-/opt/hansardgpt}"
OPT="${WAITING_OPT:-$(dirname "$DIR")}"
STATE="${WAITING_STATE:-/var/lib/wheretheystand/waiting}"
mkdir -p "$STATE"

env_value() {  # the value of $2 in the .env file $1
  grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

look() {  # name, domain, its deploy folder
  local name="$1" domain="$2" deploy="$3" var token cli answer counts reports statements oldest before
  var="$(grep -oE '^[A-Z]+_CHECK_TOKEN=' "$deploy/.env" 2>/dev/null | head -1 | tr -d '=')"
  token="$( [[ -n "$var" ]] && env_value "$deploy/.env" "$var")"
  if [[ -z "$token" ]]; then
    logger -t wheretheystand-waiting "$name: no check token in its .env, so not looked at"
    return
  fi
  cli="$(tr '[:upper:]' '[:lower:]' <<<"${var%_CHECK_TOKEN}")"
  answer="$(curl -s --max-time 20 -H "Authorization: Bearer $token" "https://$domain/api/check" 2>/dev/null)"
  # "reports statements oldest", or nothing if the answer isn't a site's check
  counts="$(python3 -c '
import json, sys
try:
    w = json.loads(sys.stdin.read())["waiting"]
    print(int(w["reports"]), int(w["statements"]), int(w.get("oldest_working_days") or 0))
except Exception:
    pass
' <<<"$answer")"
  if [[ -z "$counts" ]]; then
    logger -t wheretheystand-waiting "$name: no check answer, so not looked at"
    return
  fi
  read -r reports statements oldest <<<"$counts"
  before="$(cat "$STATE/$domain" 2>/dev/null || true)"
  echo "$reports $statements" > "$STATE/$domain"
  local had_reports=0 had_statements=0
  [[ -n "$before" ]] && read -r had_reports had_statements <<<"$before"
  (( reports > had_reports || statements > had_statements )) || return 0

  local new=() what
  (( reports > had_reports )) && new+=("$(( reports - had_reports )) new report$( (( reports - had_reports == 1 )) || echo s)")
  (( statements > had_statements )) && new+=("$(( statements - had_statements )) new statement$( (( statements - had_statements == 1 )) || echo s)")
  what="$(IFS=,; echo "${new[*]}" | sed 's/,/ and /')"
  logger -t wheretheystand-waiting "$name: $what"
  bash "$DIR/deploy/mail.sh" alert "$name: $what" <<EOF
$name has $what waiting for you.

Waiting now: $reports report$( (( reports == 1 )) || echo s) from readers (corrections, problems with a briefing, and requests from people named), and $statements statement$( (( statements == 1 )) || echo s) under the right of reply. The oldest has waited $oldest working day$( (( oldest == 1 )) || echo s). The sites promise a reply within five working days.

To read them, on the server:
  cd $deploy && docker compose run --rm -T app $cli waiting
then '$cli reports' or '$cli statements' to answer and close each one.

This email gives counts only: what was said stays on the server.
EOF
}

look "Where MPs Stand" "$(env_value "$DIR/deploy/.env" DOMAIN)" "$DIR/deploy"
for pair in $(env_value "$DIR/deploy/.env" SITES); do
  domain="${pair%%=*}"; app="${pair#*=}"
  [[ -n "$app" && "$app" != "$pair" && -f "$OPT/$app/deploy/.env" ]] || continue
  look "$(python3 -c 'import sys; d=sys.argv[1].split(".")[0]; print("Where " + d[5:-5].capitalize() + " Stand")' "$domain" 2>/dev/null || echo "$domain")" "$domain" "$OPT/$app/deploy"
done
exit 0

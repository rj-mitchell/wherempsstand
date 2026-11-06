#!/usr/bin/env bash
# Restart any site on this server that has stopped answering, and email the alerts address. Each site's app has a
# Docker health check (its /api/health, inside its own container, every 30 seconds), and Docker marks the app
# "unhealthy" after three failures in a row but does not restart it by itself. wheretheystand-watchdog.timer runs
# this every two minutes to do so, for Where MPs Stand and for each site in SITES in deploy/.env (each in its own
# folder, /opt/<container>; a site not installed there yet is skipped). Changes nothing while every site answers.
#
# Careful, so that it never makes things worse:
# - an app that is not running, or still starting, is left alone (an update is rebuilding it, say);
# - while an update holds the server-wide update lock (/var/lock/wheretheystand-update.lock), no site is restarted:
#   a site slowed by a long update is waited for, and the alert says so; the lock is held while a site restarts, so
#   no update starts meanwhile;
# - after a restart, the same site is not restarted again for 10 minutes, then 20 after the next, and after three
#   restarts in six hours not at all until the oldest of them is six hours old: the alert then says a person is needed.
# Every restart, wait and refusal is written to the system log: journalctl -t wheretheystand-watchdog
set -uo pipefail
DIR="${DIR:-/opt/hansardgpt}"
OPT="${WATCHDOG_OPT:-/opt}"
LOCK="${UPDATE_LOCK:-/var/lock/wheretheystand-update.lock}"
STATE="${WATCHDOG_STATE:-/var/lib/wheretheystand/watchdog}"
WAIT=$(( ${WATCHDOG_WAIT_MINUTES:-10} * 60 ))  # after a restart; doubled after each further one
MAX="${WATCHDOG_MAX_RESTARTS:-3}"               # restarts of one site within WINDOW, then it gives up
WINDOW=$(( ${WATCHDOG_WINDOW_HOURS:-6} * 3600 ))
UNIT=wheretheystand-watchdog.service
mkdir -p "$STATE"

say() { logger -t wheretheystand-watchdog "$1"; echo "$1"; }

# ALERT_KEY keeps each site's alerts, and each kind, apart in alert.sh's once-in-six-hours rule
alert() { ALERT_KEY="watchdog-$1" bash "$DIR/deploy/alert.sh" "$UNIT" "$2" || true; }

# the server-wide update lock, taken without waiting; held from then until this run ends
have_lock=""
take_lock() {
  [[ -n "$have_lock" ]] && return 0
  if ! exec 9>>"$LOCK"; then
    say "could not open the update lock $LOCK; carrying on without it"
    have_lock=1
    return 0
  fi
  flock -n 9 || return 1
  have_lock=1
}

# The health of a site's app: healthy, unhealthy, starting, or nothing (not running, or no health check).
# A one-off container (an update's "docker compose run app ...") is not the site, and is skipped.
health() {
  local ids id oneoff status
  ids="$(cd "$1" && docker compose ps -q app 2>/dev/null)" || return 0
  for id in $ids; do
    oneoff="" status=""
    read -r oneoff status < <(docker inspect --format \
      '{{index .Config.Labels "com.docker.compose.oneoff"}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$id" 2>/dev/null)
    [[ "${oneoff:-}" == True ]] && continue
    echo "${status:-}"
    return 0
  done
}

check() {
  local name="$1" dir="$2" key="$3" status now hist recent n last pause t
  status="$(health "$dir")"
  [[ "$status" == unhealthy ]] || return 0

  if ! take_lock; then
    say "$name is not answering, but an update holds the update lock: not restarting it while the update runs"
    alert "$key-waiting" "$name has stopped answering its health check while an update is running (it holds the \
server-wide update lock). The watchdog leaves it alone until the update has finished, then restarts it if it is \
still not answering.

Which update holds the lock: fuser -v $LOCK"
    return 0
  fi

  now="$(date +%s)"
  hist="$STATE/$key"
  recent=()
  if [[ -f "$hist" ]]; then
    while read -r t; do
      [[ "$t" =~ ^[0-9]+$ ]] && (( now - t < WINDOW )) && recent+=("$t")
    done < "$hist"
  fi
  n=${#recent[@]}
  if (( n >= MAX )); then
    say "$name is not answering; restarted $n times in the last $(( WINDOW / 3600 )) hours, so not again"
    alert "$key-stuck" "$name has stopped answering its health check again. The watchdog has restarted it $n times \
in the last $(( WINDOW / 3600 )) hours, which has not fixed it, so it is not restarting it again for now: a person \
needs to look.

To look on the server: cd $dir && docker compose ps && docker compose logs --tail 100 app"
    return 0
  fi
  if (( n > 0 )); then
    last="${recent[n - 1]}"
    pause=$(( WAIT * (1 << (n - 1)) ))
    if (( now - last < pause )); then
      say "$name is not answering; restarted $(( (now - last) / 60 )) minutes ago, so waiting until $(( pause / 60 )) minutes have passed"
      return 0
    fi
  fi

  say "$name stopped answering its health check; restarting it"
  printf '%s\n' "${recent[@]}" "$now" > "$hist"
  if (cd "$dir" && docker compose restart app); then
    alert "$key" "$name stopped answering its health check, so the watchdog restarted it."
  else
    say "$name could not be restarted"
    alert "$key-failed" "$name stopped answering its health check, and the watchdog could not restart it.

To look on the server: cd $dir && docker compose ps && docker compose logs --tail 100 app"
  fi
}

check "Where MPs Stand" "$DIR/deploy" "$(basename "$DIR")"

# the other sites: "domain=container" pairs in SITES, each site in its own folder named after its container
sites="$(grep -E '^SITES=' "$DIR/deploy/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)"
for pair in $sites; do
  domain="${pair%%=*}"; app="${pair#*=}"
  [[ -n "$app" && "$app" != "$pair" && -f "$OPT/$app/deploy/docker-compose.yml" ]] || continue
  check "$domain" "$OPT/$app/deploy" "$app"
done
exit 0

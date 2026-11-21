#!/usr/bin/env bash
# Install the latest version of Where MPs Stand (whatever has been merged into `main` on GitHub).
# Run on the server, as root:
#   bash /opt/hansardgpt/deploy/update.sh
# It fetches the new code, rebuilds and restarts the site (a few seconds of downtime), refreshes the timers (the
# site's own, and the watchdog, disk check and report that serve every site), clears out old build files, and
# finishes with the health check.
#
# The order matters. The site's own timers run `hansard` commands in the image as it is at that moment, so:
# - nothing is deployed while one of the site's own jobs is running, and its timers are held while this runs, so
#   that none starts a command on the new image while the old site is still up (a command on new code may change
#   the state database for the new code, which the old site, still writing, knows nothing of);
# - the new image is tried against this server's own settings before the old site is stopped (`hansard
#   check-settings`: it reads the settings and looks at the state database, and changes nothing). If the new
#   version would refuse to start, the deploy stops there, with the old site still running;
# - the server's update lock is held from before the site is restarted until it answers, so that the watchdog
#   never restarts a site that is still making its first start (it leaves a site alone while the lock is held);
# - old images are cleared out only once the new site answers, so the one the site ran on is there to go back to;
# - the site's timer units are installed only once the new site is up: put in before a build that then failed,
#   they would run commands the old image does not have, every night.
# If anything fails, the timers that were held are started again as they were.
set -euo pipefail
DIR="${DIR:-/opt/hansardgpt}"
UNITS_DIR="${UNITS_DIR:-/etc/systemd/system}"
LOCK="${UPDATE_LOCK:-/var/lock/wheretheystand-update.lock}"
JOBS="hansard-sync hansard-backup hansard-backup-check hansard-weekly-report"
# How long the new site is given to answer: ten minutes, the same as the health check's start period in
# docker-compose.yml. A first start that moves the state database copies the file and brings its rows across
# before it answers (3 seconds for 430 MB on a test machine: ten minutes is room for some 9 GB at a tenth of that
# speed).
WAIT_TRIES="${WAIT_TRIES:-200}"   # each try is followed by a 3-second pause

echo "==> Fetching the latest version"
caddy_before="${CADDY_BEFORE:-$(sha1sum "$DIR/deploy/Caddyfile" | cut -d' ' -f1)}"
self_before=$(sha1sum "$DIR/deploy/update.sh" | cut -d' ' -f1)
commit_before="${COMMIT_BEFORE:-$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || true)}"  # to go back to
git -C "$DIR" pull --ff-only
# bash goes on reading the copy of this script it started with, so a new version only takes effect if run afresh
if [[ -z "${UPDATE_RESTARTED:-}" && "$(sha1sum "$DIR/deploy/update.sh" | cut -d' ' -f1)" != "$self_before" ]]; then
  echo "==> This update script has changed: running the new version"
  CADDY_BEFORE="$caddy_before" COMMIT_BEFORE="$commit_before" UPDATE_RESTARTED=1 exec bash "$DIR/deploy/update.sh"
fi

cd "$DIR/deploy"
for job in $JOBS; do
  case "$(systemctl is-active "$job.service" 2>/dev/null || true)" in
    active|activating)
      echo "✗ $job.service is running: wait for it to finish and run this again (docs/OPERATIONS.md, \"New code\")."
      exit 1 ;;
  esac
done
echo "==> Holding the site's timers until the new site is up"
held=""
release() {
  # whatever went wrong, the timers that were running are running again, on the units already installed
  if [[ -n "$held" ]]; then
    # shellcheck disable=SC2086
    systemctl start $held || true
  fi
}
# set before the first timer is stopped: if stopping one fails, those already stopped are started again
trap release EXIT
for job in $JOBS; do
  if systemctl is-active --quiet "$job.timer" 2>/dev/null; then
    held="$held $job.timer"   # noted first, so that a stop that fails part-way is still put right
    systemctl stop "$job.timer"
  fi
done

echo "==> Refreshing the watchdog, disk and waiting timers"
# these serve every site on this server and run scripts of this folder, not commands of the site's image: a check
# every two minutes that restarts a site that has stopped answering, an hourly check that emails at 80% full, a
# monthly report, and a look every 15 minutes for new reports and statements on any site (counts only)
cp alert@.service \
   wheretheystand-watchdog.service wheretheystand-watchdog.timer \
   wheretheystand-disk.service wheretheystand-disk.timer \
   wheretheystand-disk-report.service wheretheystand-disk-report.timer \
   wheretheystand-waiting.service wheretheystand-waiting.timer "$UNITS_DIR/"
# the watchdog that looked after this site alone, replaced by wheretheystand-watchdog
if [[ -e "$UNITS_DIR/hansard-watchdog.timer" || -e "$UNITS_DIR/hansard-watchdog.service" ]]; then
  systemctl disable --now hansard-watchdog.timer >/dev/null 2>&1 || true
  rm -f "$UNITS_DIR/hansard-watchdog.timer" "$UNITS_DIR/hansard-watchdog.service"
fi
systemctl daemon-reload
systemctl enable --now wheretheystand-watchdog.timer wheretheystand-disk.timer \
   wheretheystand-disk-report.timer wheretheystand-waiting.timer >/dev/null

if [[ ! -r /etc/wheretheystand/mail.env ]]; then
  echo "    (Alerts aren't emailed yet: set up with bash $DIR/deploy/mail.sh setup, docs/HOSTING.md)"
fi

echo "==> Making sure the server has swap space (spare memory on disk for large updates)"
bash "$DIR/deploy/swap.sh" || echo "WARNING: could not add swap; carrying on."

echo "==> Keeping the system log and apt's downloads in check"
bash "$DIR/deploy/housekeeping.sh" || echo "WARNING: could not apply the log and download limits; carrying on."

echo "==> Refreshing the web server's own settings (Cloudflare's addresses, the certificate)"
local_before=$(cat local/*.caddy local/sites/* 2>/dev/null | sha1sum | cut -d' ' -f1)
bash "$DIR/deploy/caddy-local.sh"
docker network create sites >/dev/null 2>&1 || true  # shared with the other sites on this server
local_after=$(cat local/*.caddy local/sites/* 2>/dev/null | sha1sum | cut -d' ' -f1)

echo "==> Rebuilding the site"
docker compose build

echo "==> Trying the new version against this server's settings (nothing is changed)"
# Read-only: the settings, and a look at the state database. The old site is still running. A new version that
# would refuse to start is found here, before anything of the old one is stopped or the database is touched.
if ! docker compose run --rm --no-deps -T app hansard check-settings; then
  echo "✗ The new version would not start on this server: see the lines above. Nothing has been changed: the site is"
  echo "  still running on the version it was, and its timers are as they were. Put right what it names (most often"
  echo "  a setting in /data/settings.local.toml; docs/OPERATIONS.md, \"New code\") and run this again."
  exit 1
fi
# The server's update lock, held until the site answers: the watchdog restarts no site while it is held, and no
# site's update starts. Taken without waiting: if another site's update has it, this is not the moment.
exec 8>>"$LOCK"
if ! flock -n 8; then
  echo "✗ Another site's update is running (it holds $LOCK): wait for it to finish and run this again."
  echo "  The site is still running on the version it was; only the new image has been built."
  exit 1
fi
# The app runs as its own user (10001), not root. Anything in its data or backups not yet owned by that user is
# handed over before the new version starts; on a server where nothing has been handed over yet, this takes a minute
# or two, with the site stopped meanwhile.
if docker compose run --rm --no-deps -T --user root --entrypoint sh app -c \
     'find /data /backups ! -user 10001 -print -quit | grep -q .'; then
  echo "==> Handing the site's data to its own user (once)"
  docker compose stop app
  docker compose run --rm --no-deps -T --user root --entrypoint sh app -c \
    'find /data /backups ! -user 10001 -print0 | xargs -0r chown -h 10001:10001'
fi

echo "==> Restarting the site"
docker compose up -d
if [[ "$(sha1sum Caddyfile | cut -d' ' -f1)" != "$caddy_before" || "$local_after" != "$local_before" ]]; then
  echo "==> The web server's settings changed: restarting it"
  docker compose up -d --force-recreate --no-deps caddy  # a file mounted into the container is re-read only on a fresh start
fi

echo "==> Waiting for the site to answer"
answered=""
for _ in $(seq 1 "$WAIT_TRIES"); do
  if docker compose exec -T app python -c \
       "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)" >/dev/null 2>&1; then
    answered=yes
    break
  fi
  sleep 3
done
if [[ -z "$answered" ]]; then
  echo "✗ The site did not answer within ten minutes: see docker compose -f $DIR/deploy/docker-compose.yml logs --tail 50 app"
  echo "  Its timers are as they were, and nothing has been cleared out: the image it ran on before is still here."
  echo "  To go back to the version it was on${commit_before:+ ($commit_before)}: docs/OPERATIONS.md, \"If the new site does not"
  echo "  answer\". Read it first: if the state database has been moved, going back needs deploy/undo-wts-core.sql."
  exit 1
fi
flock -u 8   # the site answers: the watchdog and the other sites' updates may carry on

# only now, with the new site answering: old images are of no more use
echo "==> Clearing out old build files"
docker image prune -f >/dev/null
# the build cache (layers kept to speed up the next build) grows with every rebuild; the last three days' is kept
docker builder prune -f --filter until=72h >/dev/null || true

# only now, with the new site up: the site's own timers' units as this version has them
echo "==> Refreshing the update, backup, backup check and weekly report timers"
for job in $JOBS; do
  cp "$job.service" "$job.timer" "$UNITS_DIR/"
done
systemctl daemon-reload
# shellcheck disable=SC2046
systemctl enable --now $(for job in $JOBS; do echo "$job.timer"; done) >/dev/null
held=""

echo "==> Checking everything is working"
# The models folder (the site's data volume, data/models) is not touched by the deploy, and must not be by hand
# until the index has been opened once on this version: before that, moving anything in it (an old copy of the
# re-ranker, say) switches search by meaning off until every vector is made again. check.sh's line about the
# models folder, from `hansard meaning-check`, says when it may be (docs/OPERATIONS.md, "The models folder").
echo "    Leave the models folder (data/models) as it is until the check below says anything in it may be moved."
bash "$DIR/deploy/check.sh"

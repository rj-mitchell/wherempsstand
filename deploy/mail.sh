#!/usr/bin/env bash
# Send email from the charity's Gmail account (wheretheystanduk@gmail.com) through Gmail's own mail server, for
# every site on this server. Sent copies land in the account's Sent folder, and replies come back to its inbox.
#
#   bash mail.sh setup               ask for the account's app password (not shown) and save it
#   bash mail.sh test                send a test message to the alerts address
#   bash mail.sh alert SUBJECT       send the text on standard input to the alerts address
#   bash mail.sh send FILE...        send prepared letters: each FILE starts with "To:" and "Subject:" lines (and
#                                    "Cc:" if wanted), then a blank line, then the text. A letter already sent is
#                                    skipped (add --again)
#   bash mail.sh send --test-to ADDRESS FILE...
#                                    send the letters only to ADDRESS, as "TEST: …", to see them as they would arrive;
#                                    their real recipients get nothing and they are not counted as sent
#
# The settings live in /etc/wheretheystand/mail.env, readable only by root:
#   MAIL_FROM=wheretheystanduk@gmail.com
#   MAIL_APP_PASSWORD=...            a Gmail app password (docs/HOSTING.md, "Email from the server")
#   MAIL_TO=wheretheystanduk@gmail.com   where alerts go
# Letters sent are listed in /var/lib/wheretheystand/sent.log.
set -uo pipefail
CONF="${MAIL_CONF:-/etc/wheretheystand/mail.env}"
STATE="${MAIL_STATE:-/var/lib/wheretheystand}"
FROM_NAME="Where They Stand"

die() { echo "mail: $*" >&2; exit 1; }

load() {
  [[ -r "$CONF" ]] || return 1
  # shellcheck disable=SC1090
  source "$CONF"
  [[ -n "${MAIL_FROM:-}" && -n "${MAIL_APP_PASSWORD:-}" ]] || return 1
  MAIL_TO="${MAIL_TO:-$MAIL_FROM}"
}

header() {  # a header value, encoded if it isn't plain ASCII ("Subject: Gwynedd – …")
  if LC_ALL=C grep -q '[^ -~]' <<<"$1"; then printf '=?UTF-8?B?%s?=' "$(printf '%s' "$1" | base64 -w0)"; else printf '%s' "$1"; fi
}

deliver() {  # deliver TO SUBJECT BODYFILE [CC]: TO and CC may each list several addresses, separated by commas
  local to="$1" subject="$2" body="$3" cc="${4:-}" msg netrc rcpt=()
  msg=$(mktemp) && netrc=$(mktemp) || die "can't make a temporary file"
  chmod 600 "$msg" "$netrc"
  printf 'machine smtp.gmail.com login %s password %s\n' "$MAIL_FROM" "$MAIL_APP_PASSWORD" > "$netrc"
  {
    printf 'From: %s <%s>\n' "$FROM_NAME" "$MAIL_FROM"
    printf 'To: %s\n' "$to"
    [[ -n "$cc" ]] && printf 'Cc: %s\n' "$cc"
    printf 'Subject: %s\n' "$(header "$subject")"
    printf 'Date: %s\n' "$(LC_ALL=C date -R)"
    printf 'Message-ID: <%s.%s@wheretheystand.uk>\n' "$(date +%s%N)" "$RANDOM"
    printf 'MIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: 8bit\n\n'
    cat "$body"
  } > "$msg"
  local a
  for a in ${to//,/ } ${cc//,/ }; do rcpt+=(--mail-rcpt "$a"); done
  # the password goes in a file only root can read, not on the command line where other processes could see it
  curl --silent --show-error --ssl-reqd --url "smtps://smtp.gmail.com:465" --netrc-file "$netrc" \
       --mail-from "$MAIL_FROM" "${rcpt[@]}" --upload-file "$msg" --crlf --max-time 60
  local rc=$?
  rm -f "$msg" "$netrc"
  return $rc
}

cmd="${1:-}"; shift || true
case "$cmd" in
  setup)
    [[ $EUID -eq 0 ]] || die "run as root"
    mkdir -p "$(dirname "$CONF")" && chmod 700 "$(dirname "$CONF")"
    read -rp "Gmail address [wheretheystanduk@gmail.com]: " from; from="${from:-wheretheystanduk@gmail.com}"
    read -rsp "App password (16 letters; not shown): " pw; echo
    pw="${pw// /}"
    [[ ${#pw} -eq 16 ]] || die "an app password is 16 letters; nothing saved"
    read -rp "Send alerts to [$from]: " to; to="${to:-$from}"
    umask 077
    printf 'MAIL_FROM=%s\nMAIL_APP_PASSWORD=%s\nMAIL_TO=%s\n' "$from" "$pw" "$to" > "$CONF"
    unset pw
    echo "Saved to $CONF. Now: bash $0 test"
    ;;
  test)
    load || die "not set up yet: bash $0 setup"
    body=$(mktemp)
    printf 'This is a test from %s. Alerts from the sites on this server will arrive like this.\n' "$(hostname)" > "$body"
    deliver "$MAIL_TO" "Test from the server" "$body" && echo "Sent to $MAIL_TO." || { rm -f "$body"; die "Gmail refused it (see above)"; }
    rm -f "$body"
    ;;
  alert)
    subject="${1:-Alert}"
    body=$(mktemp); cat > "$body"
    if ! load; then
      logger -t wheretheystand-mail "not set up, so this alert wasn't emailed: $subject"
      rm -f "$body"; exit 0
    fi
    deliver "$MAIL_TO" "$subject" "$body" || logger -t wheretheystand-mail "couldn't email the alert: $subject"
    rm -f "$body"
    ;;
  send)
    load || die "not set up yet: bash $0 setup"
    again=0; test_to=""
    while [[ "${1:-}" == --* ]]; do
      case "$1" in
        --again) again=1; shift ;;
        --test-to) test_to="${2:-}"; [[ "$test_to" == *@* ]] || die "--test-to needs an email address"; shift 2 ;;
        *) die "unknown option $1" ;;
      esac
    done
    [[ $# -gt 0 ]] || die "no letters given"
    mkdir -p "$STATE"; touch "$STATE/sent.log"
    sent=0; skipped=0; failed=0
    for f in "$@"; do
      # the header lines, up to the first blank line: To: and Subject:, and Cc: if wanted
      head_end=$(grep -n -m1 '^$' "$f" | cut -d: -f1)
      to=$(head -n "${head_end:-0}" "$f" | sed -n 's/^To: *//p')
      cc=$(head -n "${head_end:-0}" "$f" | sed -n 's/^Cc: *//p')
      subject=$(head -n "${head_end:-0}" "$f" | sed -n 's/^Subject: *//p')
      if [[ -z "$head_end" || -z "$to" || -z "$subject" ]]; then
        echo "✗ $f: must start with To: and Subject: lines (and Cc: if wanted), then a blank line"
        failed=$((failed + 1)); continue
      fi
      sum=$(sha1sum "$f" | cut -d' ' -f1)
      if [[ -n "$test_to" ]]; then
        body=$(mktemp)
        { printf '[Test copy. This letter would go to %s%s.]\n\n' "$to" "${cc:+, with a copy to $cc}"
          tail -n +"$((head_end + 1))" "$f"; } > "$body"
        if deliver "$test_to" "TEST: $subject" "$body"; then
          echo "✓ $(basename "$f"): test copy to $test_to (not sent to $to)"; sent=$((sent + 1))
        else
          echo "✗ $(basename "$f"): test copy not sent"; failed=$((failed + 1))
        fi
        rm -f "$body"; continue
      fi
      if [[ $again -eq 0 ]] && grep -qF " $(basename "$f") $to " "$STATE/sent.log"; then
        echo "- $(basename "$f"): already sent to $to"; skipped=$((skipped + 1)); continue
      fi
      body=$(mktemp); tail -n +"$((head_end + 1))" "$f" > "$body"
      if deliver "$to" "$subject" "$body" "$cc"; then
        printf '%s %s %s %s\n' "$(date -Is)" "$(basename "$f") $to" "$sum" "$subject" >> "$STATE/sent.log"
        echo "✓ $(basename "$f") to $to${cc:+ (copy to $cc)}"; sent=$((sent + 1))
        sleep "${MAIL_PAUSE:-5}"   # a few seconds apart, as a person sending them would
      else
        echo "✗ $(basename "$f") to $to: not sent"; failed=$((failed + 1))
      fi
      rm -f "$body"
    done
    echo "Sent $sent, already sent $skipped, failed $failed."
    [[ $failed -eq 0 ]]
    ;;
  *)
    sed -n '2,17p' "$0"; exit 1 ;;
esac

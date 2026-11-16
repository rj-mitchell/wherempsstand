"""A look at the state database BEFORE deploying the move onto the shared package (wts-core), read and nothing
changed.

It needs nothing but Python, opens the database read-only, and prints no address, name or word of what anyone
sent: numbers, dates, ages and sizes. It is not a `hansard` command on purpose: a `hansard` command on newly built
code, run before the site has restarted on it, would open the database and make the move itself, while the old
site was still writing (docs/OPERATIONS.md, "The state database"). So it is piped into the site as it is running:

    cd /opt/hansardgpt
    git fetch origin        # fetches the new version without changing the one that is running
    git show origin/main:deploy/before-wts-core.py | docker compose -f deploy/docker-compose.yml exec -T app python -

It says three things.

1. What will lapse. Once the new code is running, the first thing that writes to or lists reports and statements
   (a report or a statement sent by a reader, the admin page's lists, any `hansard statements` or `hansard reports`
   command, the twice-daily update, the nightly tidy) declines every statement that has waited more than three
   months, with the reason "No decision was made in time; please send it again.", and deletes its sender's address;
   and deletes the contact details of every report left open that long. There is no deciding them on the new code
   first: decide the ones that still deserve a decision on the old code, before the deploy, or let them lapse.
2. What would stop the move. The old rows are copied into the package's tables in one transaction when the new
   code first opens the database. A row the new tables cannot take stops it: the site then does not start, nothing
   is half copied, and the old tables are as they were. Mend the row, on the old code, before deploying.
3. How much room the move needs: the size of the database now, and the free space beside it. The first start keeps
   a copy of the whole file as it was, and the rows are then in the file twice until the old tables are dropped.

It ends with status 1 if anything would stop the move, and 0 otherwise.
"""

import calendar
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime

MONTHS = 3  # how long a statement waits for a decision, and an open report keeps its contact details
MOVED = "import:hansard-state"  # the mark the move leaves in wts_schema
# the old tables the move waits for (in every state database since the site's first release), and for each old
# table what the package's table must have: a row without one of these would stop the move
NEEDED = ("usage", "briefings")
REQUIRED = {
    "usage": ("at", "kind"),
    "answer_cache": ("key", "created_at", "events"),
    "failures": ("at", "kind", "error"),
    "briefings": ("id", "created_at", "question", "settings", "events"),
    "reports": ("at", "message"),
    "statements": ("at", "item_path", "name", "role", "text", "status"),
}


def months_before(when, months):
    """The same moment so many calendar months earlier (the month's last day where that day does not exist)."""
    y, m = divmod(when.year * 12 + when.month - 1 - months, 12)
    return when.replace(year=y, month=m + 1, day=min(when.day, calendar.monthrange(y, m + 1)[1]))


def _open(path):
    return sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)


def _tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(con, table):
    return {row[1] for row in con.execute("PRAGMA table_info(" + table + ")")}


def moved(con):
    """Whether the old rows have already been brought across to the shared package's tables."""
    return "wts_schema" in _tables(con) and con.execute(
        "SELECT 1 FROM wts_schema WHERE name = ?", (MOVED,)).fetchone() is not None


def would_lapse(path, now):
    """(the moment before which a thing has waited too long, [(statement, sent)], [(report, sent)]): the
    statements still waiting and the open reports with contact details, sent more than three months ago. Read from
    the old tables before the move, and from the package's after it."""
    cutoff = months_before(now, MONTHS).isoformat(timespec="seconds")
    con = _open(path)
    try:
        tables, after = _tables(con), moved(con)
        statements, reports = ("wts_statements", "wts_reports") if after else ("statements", "reports")
        waiting = con.execute("SELECT id, at FROM " + statements + " WHERE status = 'pending' AND at < ? "
                              "ORDER BY at, id", (cutoff,)).fetchall() if statements in tables else []
        left_open = con.execute("SELECT id, at FROM " + reports + " WHERE status = 'open' AND contact IS NOT NULL "
                                "AND contact != '' AND at < ? ORDER BY at, id",
                                (cutoff,)).fetchall() if reports in tables else []
        return cutoff, [tuple(r) for r in waiting], [tuple(r) for r in left_open]
    finally:
        con.close()


def would_stop_the_move(path):
    """What would stop the move, each as a line for the owner; none after the move has been made."""
    con = _open(path)
    try:
        if moved(con):
            return []
        tables, said = _tables(con), []
        if not tables & set(REQUIRED):
            return []  # a new site: nothing to bring across
        for table in NEEDED:
            if table not in tables:
                said.append("There is no " + table + " table: this is not the site's state database as the old "
                            "code keeps it, and the move would be passed over (the site would start with no "
                            "spending and no saved briefings).")
        for table, columns in REQUIRED.items():
            if table not in tables:
                continue
            have = _columns(con, table)
            for column in columns:
                if column not in have:
                    said.append("The " + table + " table has no " + column + " column: the move would stop.")
                    continue
                ids = [str(r[0]) for r in con.execute(
                    "SELECT rowid FROM " + table + " WHERE " + column + " IS NULL ORDER BY rowid LIMIT 20")]
                if ids:
                    said.append("In " + table + ", " + str(len(ids)) + (" or more rows have" if len(ids) == 20
                                                                        else " row(s) have")
                                + " no " + column + " and would stop the move (rowid " + ", ".join(ids) + "). To "
                                "mend: DELETE FROM " + table + " WHERE " + column + " IS NULL;")
        site = con.execute("SELECT value FROM wts_kv WHERE key = 'site'").fetchone() if "wts_kv" in tables else None
        if site and site[0] != "hansard":
            said.append("This database is marked as another site's (" + str(site[0]) + "): the new code would "
                        "refuse to open it.")
        return said
    finally:
        con.close()


def lines(path, now):
    """(what to tell the owner, whether anything would stop the move)."""
    def age(at):
        return (now.date() - datetime.fromisoformat(at).date()).days

    cutoff, statements, reports = would_lapse(path, now)
    said = ["Sent before " + cutoff[:10] + " and still undecided: " + str(len(statements)) + " statements, "
            + str(len(reports)) + " reports with contact details."]
    for sid, at in statements:
        said.append("  statement " + str(sid) + ": sent " + at[:10] + ", " + str(age(at)) + " days ago: it would be "
                    "declined as lapsed and its sender's address deleted. To decide it first: hansard statements "
                    "publish " + str(sid) + ' --checked "...", or hansard statements decline ' + str(sid)
                    + ' --reason "...".')
    for rid, at in reports:
        said.append("  report " + str(rid) + ": sent " + at[:10] + ", " + str(age(at)) + " days ago: it would stay "
                    "open, and its contact details would be deleted. To reply first, see the admin page's "
                    "Reports.")
    stops = would_stop_the_move(path)
    said += stops
    con = _open(path)
    try:
        done = moved(con)
    finally:
        con.close()
    if done:
        said.append("This database has already been moved onto the shared package.")
    elif not stops:
        said.append("Nothing would stop the move.")
    return said, bool(stops)


def room(path):
    """The size of the database (with its write-ahead log) and the free space beside it, as a line."""
    size = sum(os.path.getsize(str(path) + suffix) for suffix in ("", "-wal") if os.path.exists(str(path) + suffix))
    free = shutil.disk_usage(os.path.dirname(os.path.abspath(str(path)))).free
    line = (f"The database is {size / 1e6:.0f} MB, and {free / 1e6:.0f} MB are free beside it. The first start keeps a copy of it as it "
            f"was ({size / 1e6:.0f} MB, deleted after 14 days) and brings its rows across (about as much again, until the old "
            "tables are dropped on 1 February 2027); the first night's tidy needs about as much again for a short "
            "while.")
    if free < 3 * size:
        line += " THAT IS LESS THAN THREE TIMES ITS SIZE: free some space before deploying."
    return line


if __name__ == "__main__":
    database = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("HANSARD_DATA_DIR", "/data"),
                                                                 "state.sqlite")
    moment = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(UTC)
    if not os.path.exists(database):
        print("There is no state database at " + database + " yet (a first install): nothing to look at.")
        sys.exit(0)
    told, stopped = lines(database, moment)
    print("\n".join(told))
    print(room(database))
    sys.exit(1 if stopped else 0)

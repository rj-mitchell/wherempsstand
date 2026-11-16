-- Making the move onto the shared package (wts-core) again, from the site's old tables, when the code from before
-- the move has written to the database since it was moved. docs/OPERATIONS.md, "Going back, and coming forward
-- again", says when and how to run this; the new code refuses to start on such a database and names this file.
--
-- VALID ONLY UNTIL 1 FEBRUARY 2027, like deploy/undo-wts-core.sql: after that day the old tables are gone.
--
-- When it is needed. The move is made once, and marked. If the old code is then started on the database without
-- deploy/undo-wts-core.sql having been run first (after a deploy of the new code that did not start, say), what
-- it takes in goes to the old tables only, and the new code would never see it: a briefing saved then would
-- answer 404, and its cost would not count towards the caps. This file forgets the move, so that the next start of
-- the new code brings every row of the old tables across afresh.
--
--   cd /opt/hansardgpt/deploy
--   docker compose stop app
--   docker compose run --rm --no-deps -T app python -c "import sqlite3, sys; con = sqlite3.connect('/data/state.sqlite'); con.executescript(sys.stdin.read())" < redo-wts-core.sql
--   docker compose up -d
--
-- WHAT THIS DISCARDS. Everything the NEW code has written to its own tables since the move, which the old tables
-- do not hold: spending rows (so the day's and the month's totals fall by their cost), saved briefings (their
-- addresses then answer "no briefing at this address"), cached answers, notes of failed briefings, reports and
-- statements sent or decided on the new code, and the log of what was done to each statement. If the new code
-- never answered a reader after the move (it did not start), that is nothing. The message the new code refuses to
-- start with counts both sides: read it first. If the new code's rows are the ones to keep, run
-- deploy/undo-wts-core.sql instead, which carries them to the old tables and discards what the old code wrote
-- since the move.
--
-- Kept as they are: the old tables, the site's own tables, the record of each briefing, and the list of hidden
-- briefings (a briefing hidden on the new code is hidden again when it starts).
BEGIN IMMEDIATE;

-- Only on a database that has been moved, and only once. Without the move's mark there is no move to forget:
-- the line below then fails ("CHECK constraint failed"), and nothing is changed.
CREATE TEMP TABLE the_move_was_made (yes INTEGER NOT NULL CHECK (yes = 1));
INSERT INTO the_move_was_made SELECT count(*) FROM wts_schema WHERE name = 'import:hansard-state';
DROP TABLE the_move_was_made;

-- Only while the old tables are there to make the move again from. From 1 February 2027 the nightly tidy drops
-- them, and this file would then empty the package's tables with nothing to bring their rows back: the two lines
-- below then fail ("no such table: usage"), before anything is deleted, and nothing is changed.
SELECT count(*) FROM usage;
SELECT count(*) FROM briefings;

DELETE FROM wts_spending;
-- what the shared package (0.3.0) keeps in summary of the spending rows just emptied: each signed-in account's
-- calls and cost by the month, and the public's visitors by the month (the rows whose day is a month). Left
-- behind, the next import brings the same spending back beside them, and the report of who it was for counts
-- it twice. (The day's count of all visitors, under '*', is kept: the rows that come back carry no codes.)
DELETE FROM wts_account_months;
DELETE FROM wts_visitor_days WHERE who != '*';
DELETE FROM wts_answer_cache;
DELETE FROM wts_failures;
DELETE FROM wts_briefings;
DELETE FROM wts_reports;
DELETE FROM wts_statement_log;
DELETE FROM wts_statements;
DELETE FROM wts_reply_counts;
DELETE FROM wts_kv WHERE key LIKE 'visitor_salt:%';
DELETE FROM wts_schema WHERE name = 'import:hansard-state';

COMMIT;

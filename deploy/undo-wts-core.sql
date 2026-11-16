-- Going back to the site's code as it was before the shared package (wts-core), without losing what was asked,
-- sent or decided since. docs/OPERATIONS.md, "The state database", says when and how to run this.
--
-- VALID ONLY UNTIL 1 FEBRUARY 2027. On that day the site drops its old tables (src/hansard/store/wts.py,
-- OLD_TABLES), and this file, which writes them again, has nothing to write to: it fails at its first statement and
-- changes nothing. Do not run it after that day: from then, going back means putting back a backup made before the
-- move, while one is still kept.
--
-- Since the move, spending, the answer cache, saved briefings, the notes of failed briefings, reports and
-- statements are kept in the package's tables (wts_spending, wts_answer_cache, wts_briefings, wts_failures,
-- wts_reports, wts_statements, wts_statement_log, and the day's visitor secret in wts_kv). The site's old tables
-- (usage, answer_cache, briefings, failures, reports, statements, and the secret in kv) were left in place on the
-- day of the move, and the old code reads only those. Each night since, what the site has promised to delete has
-- been deleted from them too (a sender's address, a report's contact details, old notes of failed briefings, old
-- visitor secrets and codes). This writes the old tables again from the package's, in one transaction, so the old
-- code finds everything the site still keeps: what it had, and what has come in since. The site's own tables
-- (accounts, the update history, visit and question counts, feedback, topics, featured briefings) were never
-- moved, and are not touched.
--
--   cd /opt/hansardgpt/deploy
--   docker compose stop app
--   docker compose run --rm --no-deps -T app python -c "import sqlite3, sys; con = sqlite3.connect('/data/state.sqlite'); con.executescript(sys.stdin.read())" < undo-wts-core.sql
--   (then check out the version before the move, and: docker compose up -d --build)
--
-- Run it BEFORE the old code is started. Never start the old code on a moved database without it: what the new
-- code took in since the move is in the new tables only, and the old code would not see it.
--
-- What the old tables can't hold is folded into what they can: a statement taken down after it was published is
-- "declined" there, with the time it was published kept (that is how the old code recorded one), and so is one
-- that lapsed undecided; a statement updated since it was published is shown with the date of the update, as the
-- old code showed it; the log of what was done to each statement is not kept, but for the note of how its sender
-- was confirmed; a briefing hidden while a concern is decided is shown again (the old code has no way to hide
-- one: withdraw it on the old code if it must stay down). A visitor's code that was forgotten after its day stays
-- forgotten, and no code is put back on a report or a statement. The record of each briefing (wts_briefing_log)
-- has no place in the old tables and stays where it is, unread.
--
-- WHAT IT GIVES BACK OF THE SPENDING OF DAYS GONE BY. Once a day is over, the shared package (from version 0.3.0)
-- forgets on each of that day's spending rows who the call was for and the time of day: the visitor's code, the
-- signed-in account and any mark that the call was an account's go, and the time becomes the day's midnight. So a
-- spending row of a day gone by comes back here with no user_id, no visitor and a time of 00:00:00 on its day: the
-- old code's report counts it among the calls that were nobody's, and not as the public's or an account's. Its
-- cost, its token counts and its day are exact, and so are the daily and monthly caps. Which account an earlier
-- day's call was for is not known to anything any more: each account's calls and cost by the calendar month are in
-- wts_account_months, which the old tables have no place for, and which is emptied below with the spending rows.
-- Today's rows come back whole.
--
-- WHAT THIS DISCARDS. Anything the OLD code has written to the old tables since the move (it can have written
-- there only if it was started again after the move without this file being run first): the old tables are
-- written afresh from the new ones, so such rows go. If the old code has run since the move and its rows are the
-- ones to keep, run deploy/redo-wts-core.sql instead, and read what that one discards.
--
-- It ends by forgetting the move: the package's copies of these rows are emptied and the move's mark is removed.
-- The database is then as the old code keeps it, and the old code can run on it for as long as need be. When the
-- new code is started again, it makes the move afresh from the old tables, with whatever the old code has taken
-- in meanwhile. (The log of each statement starts again then, with one entry, "imported"; a briefing that was
-- hidden is hidden again, since the list of hidden briefings is kept.)
BEGIN IMMEDIATE;

-- Only on a database that has been moved, and only once. Without the move's mark this file would write the old tables afresh from empty ones:
-- the line below then fails ("CHECK constraint failed"), and nothing is changed.
CREATE TEMP TABLE the_move_was_made (yes INTEGER NOT NULL CHECK (yes = 1));
INSERT INTO the_move_was_made SELECT count(*) FROM wts_schema WHERE name = 'import:hansard-state';
DROP TABLE the_move_was_made;

-- Only while the old tables are there to write again: from 1 February 2027 the nightly tidy drops them, and the
-- first line below then fails ("no such table: usage"), before anything is deleted, and nothing is changed.
SELECT count(*) FROM usage;
SELECT count(*) FROM briefings;

DELETE FROM usage;
INSERT INTO usage (id, user_id, at, kind, model, input_tokens, output_tokens, cache_read_tokens,
                   cache_write_tokens, cost_usd, visitor)
SELECT id, account, at, kind, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
       nullif(visitor, '')
FROM wts_spending;

DELETE FROM kv WHERE key LIKE 'visitor_salt:%';
INSERT INTO kv (key, value) SELECT key, value FROM wts_kv WHERE key LIKE 'visitor_salt:%';

DELETE FROM answer_cache;
INSERT INTO answer_cache (key, created_at, events) SELECT key, created_at, events FROM wts_answer_cache;

DELETE FROM failures;
INSERT INTO failures (id, at, kind, question, error) SELECT id, at, kind, question, error FROM wts_failures;

DELETE FROM briefings;
INSERT INTO briefings (id, created_at, question, settings, events, records_up_to, model, withdrawn_at,
                       withdrawn_reason)
SELECT id, created_at, question, options, events, json_extract(extra, '$.records_up_to'),
       json_extract(extra, '$.model'), withdrawn_at, withdrawn_reason
FROM wts_briefings;

DELETE FROM reports;
INSERT INTO reports (id, at, kind, briefing_id, page, message, contact, status, closed_at, resolution, public_note)
SELECT id, at, coalesce(kind, 'general'), briefing_id, page, message, contact, status, closed_at, resolution,
       public_note
FROM wts_reports;

DELETE FROM statements;
INSERT INTO statements (id, at, name, role, item_path, person, text, link, email, status, published_at, updated_at,
                        closed_at, reason, checked)
SELECT s.id, s.at, s.name, s.role, s.item, CAST(s.about AS INTEGER), s.text, s.link, s.email,
       CASE s.status WHEN 'removed' THEN 'declined' ELSE s.status END,
       -- the old code shows a statement with the date of the words now shown: the later of publishing and the
       -- last update
       CASE WHEN s.published_at IS NOT NULL AND s.updated_at IS NOT NULL AND s.updated_at > s.published_at
            THEN s.updated_at ELSE s.published_at END,
       s.updated_at, s.closed_at, s.reason,
       (SELECT l.note FROM wts_statement_log l
        WHERE l.statement = s.id AND l.action IN ('published', 'imported') AND l.note IS NOT NULL AND l.note != ''
        ORDER BY l.id DESC LIMIT 1)
FROM wts_statements s;

-- and the move is forgotten, so that the new code makes it afresh from the old tables when it is next started
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

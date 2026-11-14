BEGIN TRANSACTION;
CREATE TABLE answer_cache (
    key        TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    events     TEXT NOT NULL
);
INSERT INTO "answer_cache" VALUES('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','2026-11-13T11:00:00+00:00','[{"type": "plan", "plan": {"question": "What has been said about trial by jury since 2020?"}, "usage": null}, {"type": "sources", "sources": [{"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition"}, {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"}], "candidates": 12, "semantic": true, "evidence": {"records": 2}}, {"type": "text", "text": "Hilary Benn said\u2026"}, {"type": "final", "answer": {"segments": [{"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1], "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [], "misquotes": []}, {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2], "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [], "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []}], "markdown": "Hilary Benn said that trial by jury was \"a cornerstone\". In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This was a turning point for every member in the Chamber.", "no_records": false, "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2, "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}, "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "interests": [{"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn", "register": "Register of Members'' Financial Interests", "link": "/people/10669#h-interests", "items": [{"category": "3 Gifts", "summary": "Bar Council - \u00a3500.00", "registered": "2021-01-05", "connection": "The gift is from the barristers'' professional body and the speech is on jury trial.", "kind": "register", "register": "commons", "url": null}, {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice", "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register", "register": "commons", "url": null}]}, {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke", "register": "Register of Members'' Financial Interests", "link": "/people/10115#h-interests", "items": [{"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01", "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": null}]}], "usage": null, "cached": true, "briefing_key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "briefing_id": "briefingaa", "prepared_at": "2026-10-25T12:00:00+00:00", "records_up_to": "2026-10-24"}]');
INSERT INTO "answer_cache" VALUES('cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc','2026-11-13T11:00:00+00:00','[{"type": "plan", "plan": {"question": "What has been said about trial by jury since 2020?"}, "usage": null}, {"type": "sources", "sources": [{"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition"}, {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"}], "candidates": 12, "semantic": true, "evidence": {"records": 2}}, {"type": "text", "text": "Hilary Benn said\u2026"}, {"type": "final", "answer": {"segments": [{"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1], "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [], "misquotes": []}, {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2], "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [], "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []}], "markdown": "Hilary Benn said that trial by jury was \"a cornerstone\". In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This was a turning point for every member in the Chamber.", "no_records": false, "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2, "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}, "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "interests": [{"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn", "register": "Register of Members'' Financial Interests", "link": "/people/10669#h-interests", "items": [{"category": "3 Gifts", "summary": "Bar Council - \u00a3500.00", "registered": "2021-01-05", "connection": "The gift is from the barristers'' professional body and the speech is on jury trial.", "kind": "register", "register": "commons", "url": null}, {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice", "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register", "register": "commons", "url": null}]}, {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke", "register": "Register of Members'' Financial Interests", "link": "/people/10115#h-interests", "items": [{"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01", "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": null}]}], "usage": null, "cached": true}]');
INSERT INTO "answer_cache" VALUES('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','2026-11-01T00:00:00+00:00','[{"type": "final", "answer": null}]');
CREATE TABLE briefings (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    question       TEXT NOT NULL,
    settings       TEXT NOT NULL,
    events         TEXT NOT NULL,
    records_up_to  TEXT,
    model          TEXT,
    withdrawn_at   TEXT,
    withdrawn_reason TEXT
);
INSERT INTO "briefings" VALUES('briefingaa','2026-10-25T12:00:00+00:00','What has been said about trial by jury since 2020?','{"mode": "summary", "recency": null, "role_weight": null, "scope": {"written": true, "pbc": true, "votes": true, "edms": true, "devolved": true, "evidence": true}, "plan": null, "sources_only": false, "follows": null, "compare": []}','[{"type": "plan", "plan": {"question": "What has been said about trial by jury since 2020?"}, "usage": null}, {"type": "sources", "sources": [{"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition"}, {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"}], "candidates": 12, "semantic": true, "evidence": {"records": 2}}, {"type": "final", "answer": {"segments": [{"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1], "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [], "misquotes": []}, {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2], "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [], "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []}], "markdown": "Hilary Benn said that trial by jury was \"a cornerstone\". In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This was a turning point for every member in the Chamber.", "no_records": false, "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2, "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}, "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "interests": [{"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn", "register": "Register of Members'' Financial Interests", "link": "/people/10669#h-interests", "items": [{"category": "3 Gifts", "summary": "Bar Council - \u00a3500.00", "registered": "2021-01-05", "connection": "The gift is from the barristers'' professional body and the speech is on jury trial.", "kind": "register", "register": "commons", "url": null}, {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice", "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register", "register": "commons", "url": null}]}, {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke", "register": "Register of Members'' Financial Interests", "link": "/people/10115#h-interests", "items": [{"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01", "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": null}]}]}]','2026-10-24','claude-sonnet-5-5',NULL,NULL);
INSERT INTO "briefings" VALUES('briefingab','2026-11-05T12:00:00+00:00','And what did peers say?','{"mode": "comprehensive", "recency": null, "role_weight": null, "scope": {"written": true, "pbc": true, "votes": true, "edms": true, "devolved": true, "evidence": true}, "plan": null, "sources_only": false, "follows": null, "compare": []}','[{"type": "plan", "plan": {"question": "And what did peers say?"}, "usage": null}, {"type": "sources", "sources": [{"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition"}, {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"}], "candidates": 12, "semantic": true, "evidence": {"records": 2}}, {"type": "final", "answer": {"segments": [{"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1], "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [], "misquotes": []}, {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2], "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [], "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []}], "markdown": "Hilary Benn said that trial by jury was \"a cornerstone\". In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This was a turning point for every member in the Chamber.", "no_records": false, "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2, "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}, "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "interests": [{"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn", "register": "Register of Members'' Financial Interests", "link": "/people/10669#h-interests", "items": [{"category": "3 Gifts", "summary": "Bar Council - \u00a3500.00", "registered": "2021-01-05", "connection": "The gift is from the barristers'' professional body and the speech is on jury trial.", "kind": "register", "register": "commons", "url": null}, {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice", "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register", "register": "commons", "url": null}]}, {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke", "register": "Register of Members'' Financial Interests", "link": "/people/10115#h-interests", "items": [{"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01", "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": null}]}]}]','2026-11-04','claude-opus-5-5',NULL,NULL);
INSERT INTO "briefings" VALUES('briefingac','2026-11-06T12:00:00+00:00','An older question?','{}','[{"type": "plan", "plan": {"question": "An older question?"}, "usage": null}, {"type": "sources", "sources": [{"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition"}, {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"}], "candidates": 12, "semantic": true, "evidence": {"records": 2}}, {"type": "final", "answer": {"segments": [{"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1], "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [], "misquotes": []}, {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2], "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [], "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []}], "markdown": "Hilary Benn said that trial by jury was \"a cornerstone\". In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This was a turning point for every member in the Chamber.", "no_records": false, "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2, "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}, "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "interests": [{"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn", "register": "Register of Members'' Financial Interests", "link": "/people/10669#h-interests", "items": [{"category": "3 Gifts", "summary": "Bar Council - \u00a3500.00", "registered": "2021-01-05", "connection": "The gift is from the barristers'' professional body and the speech is on jury trial.", "kind": "register", "register": "commons", "url": null}, {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice", "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register", "register": "commons", "url": null}]}, {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke", "register": "Register of Members'' Financial Interests", "link": "/people/10115#h-interests", "items": [{"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01", "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": null}]}]}]',NULL,NULL,'2026-11-07T09:00:00+00:00','It quoted the wrong debate.');
INSERT INTO "briefings" VALUES('briefingad','2026-11-03T07:15:20+00:00','trial by jury','{"mode": "summary", "topic": "trial-by-jury"}','[{"type": "plan", "plan": {"question": "trial by jury"}, "usage": null}, {"type": "sources", "sources": [{"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition"}, {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"}], "candidates": 12, "semantic": true, "evidence": {"records": 2}}, {"type": "final", "answer": {"segments": [{"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1], "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [], "misquotes": []}, {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2], "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited", "loaded": [], "misquotes": []}, {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [], "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []}], "markdown": "Hilary Benn said that trial by jury was \"a cornerstone\". In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This was a turning point for every member in the Chamber.", "no_records": false, "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2, "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}, "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "interests": [{"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn", "register": "Register of Members'' Financial Interests", "link": "/people/10669#h-interests", "items": [{"category": "3 Gifts", "summary": "Bar Council - \u00a3500.00", "registered": "2021-01-05", "connection": "The gift is from the barristers'' professional body and the speech is on jury trial.", "kind": "register", "register": "commons", "url": null}, {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice", "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register", "register": "commons", "url": null}]}, {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke", "register": "Register of Members'' Financial Interests", "link": "/people/10115#h-interests", "items": [{"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01", "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": null}]}]}]','2026-11-02','claude-sonnet-5-5',NULL,NULL);
CREATE TABLE day_events (
    day     TEXT NOT NULL,
    event   TEXT NOT NULL,
    visitor TEXT NOT NULL,
    PRIMARY KEY (day, event, visitor)
);
INSERT INTO "day_events" VALUES('2026-11-13','ask','a1a1a1a1a1a1a1a1a1a1a1a1');
CREATE TABLE day_totals (
    day      TEXT PRIMARY KEY,
    visitors INTEGER NOT NULL
);
CREATE TABLE day_visitors (
    day     TEXT NOT NULL,
    visitor TEXT NOT NULL,
    PRIMARY KEY (day, visitor)
);
INSERT INTO "day_visitors" VALUES('2026-11-13','a1a1a1a1a1a1a1a1a1a1a1a1');
CREATE TABLE event_totals (
    day      TEXT NOT NULL,
    event    TEXT NOT NULL,
    visitors INTEGER NOT NULL,
    PRIMARY KEY (day, event)
);
CREATE TABLE failures (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    question  TEXT,
    error     TEXT NOT NULL
);
INSERT INTO "failures" VALUES(1,'2026-08-01T00:00:00+00:00','briefing','An old question?','RuntimeError: an old failure');
INSERT INTO "failures" VALUES(2,'2026-11-12T23:00:00+00:00','question reading','What has been said about trial by jury since 2020?','AuthenticationError: bad key');
INSERT INTO "failures" VALUES(3,'2026-11-13T07:15:00+00:00','topic','trial by jury','no briefing was written');
INSERT INTO "failures" VALUES(4,'2026-11-13T11:30:00+00:00','briefing','And what did peers say?','The model declined to answer this question.');
INSERT INTO "failures" VALUES(5,'2026-11-13T11:31:00+00:00','briefing','','xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx');
CREATE TABLE featured (
    briefing_id  TEXT PRIMARY KEY,
    title        TEXT,
    blurb        TEXT,
    featured_at  TEXT NOT NULL
);
INSERT INTO "featured" VALUES('briefingaa','Trial by jury since 2020',NULL,'2026-11-07T09:00:00+00:00');
CREATE TABLE feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    briefing_key TEXT,
    question     TEXT NOT NULL,
    useful       INTEGER NOT NULL,
    visitor      TEXT
);
INSERT INTO "feedback" VALUES(1,'2026-11-13T11:40:00+00:00','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','What has been said about trial by jury since 2020?',1,'a1a1a1a1a1a1a1a1a1a1a1a1');
INSERT INTO "feedback" VALUES(2,'2026-11-13T11:40:00+00:00',NULL,'And what did peers say?',0,NULL);
CREATE TABLE kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO "kv" VALUES('visitor_salt:2026-11-13','5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a');
INSERT INTO "kv" VALUES('visitor_salt','0d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0d');
INSERT INTO "kv" VALUES('some_other_key','kept');
INSERT INTO "kv" VALUES('supporter:2026-11-13:b2b2b2b2b2b2b2b2b2b2b2b2','1');
CREATE TABLE manifest (
    source      TEXT NOT NULL,
    key         TEXT NOT NULL,
    revision    TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    parsed_at   TEXT NOT NULL,
    indexed_at  TEXT,
    embedded_at TEXT,
    PRIMARY KEY (source, key)
);
INSERT INTO "manifest" VALUES('debates','debates/debates2026-11-12','a','debates2026-11-12a.xml',1,1,1,'2026-11-13T12:00:00+00:00',NULL,NULL);
CREATE TABLE page_views (
    day   TEXT NOT NULL,
    path  TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, path)
);
INSERT INTO "page_views" VALUES('2026-11-13','/people/10669',1);
CREATE TABLE question_counts (
    day       TEXT NOT NULL,
    question  TEXT NOT NULL,
    n         INTEGER NOT NULL,
    PRIMARY KEY (day, question)
);
INSERT INTO "question_counts" VALUES('2026-11-13','what has been said about trial by jury since 2020?',1);
CREATE TABLE referrers (
    day   TEXT NOT NULL,
    host  TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, host)
);
INSERT INTO "referrers" VALUES('2026-11-13','example.org',1);
CREATE TABLE reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    kind         TEXT NOT NULL,
    briefing_id  TEXT,
    page         TEXT,
    message      TEXT NOT NULL,
    contact      TEXT,
    visitor      TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    closed_at    TEXT,
    resolution   TEXT,
    public_note  TEXT
);
INSERT INTO "reports" VALUES(1,'2026-07-01T09:00:00+00:00','general',NULL,NULL,'An old report, never closed.','old@example.org','0f0f0f0f0f0f0f0f0f0f0f0f','open',NULL,NULL,NULL);
INSERT INTO "reports" VALUES(2,'2026-11-10T09:00:00+00:00','correction','briefingaa',NULL,'The date of the debate is wrong.','reader@example.org','1e1e1e1e1e1e1e1e1e1e1e1e','open',NULL,NULL,NULL);
INSERT INTO "reports" VALUES(3,'2026-11-11T09:00:00+00:00','takedown',NULL,'/debates/2021-03-04/commons/trial-by-jury','Please remove my name.','named@example.org','a1a1a1a1a1a1a1a1a1a1a1a1','open',NULL,NULL,NULL);
INSERT INTO "reports" VALUES(4,'2026-11-12T09:00:00+00:00','complaint',NULL,'/people/10669','A loaded word about a member.',NULL,NULL,'open',NULL,NULL,NULL);
INSERT INTO "reports" VALUES(5,'2026-11-12T10:00:00+00:00','correction','briefingab',NULL,'Closed the same day.',NULL,NULL,'resolved','2026-11-12T15:00:00+00:00','The briefing was corrected.','A briefing gave the wrong date; corrected.');
INSERT INTO "reports" VALUES(6,'2026-11-12T11:00:00+00:00','general',NULL,NULL,'Dismissed the same day.',NULL,NULL,'dismissed','2026-11-12T15:00:00+00:00','Nothing to change.',NULL);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE statements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL,
    item_path    TEXT NOT NULL,
    person       INTEGER,
    text         TEXT NOT NULL,
    link         TEXT,
    email        TEXT,
    visitor      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    published_at TEXT,
    updated_at   TEXT,
    closed_at    TEXT,
    reason       TEXT,
    checked      TEXT
);
INSERT INTO "statements" VALUES(1,'2026-11-08T09:00:00+00:00','Hilary Benn','MP for Leeds South','/b/briefingaa',10669,'New words, exactly as the author sent them.','https://hansard.parliament.uk/commons/2021-03-04','office@example.org','a1a1a1a1a1a1a1a1a1a1a1a1','published','2026-11-10T09:00:00+00:00','2026-11-10T09:00:00+00:00',NULL,NULL,'Replied from their parliament.uk address, 9 November.');
INSERT INTO "statements" VALUES(2,'2026-11-12T09:00:00+00:00','Still Waiting','MP for Leeds South','/people/10669',10669,'A statement not yet decided at all.',NULL,'office@example.org',NULL,'pending',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "statements" VALUES(3,'2026-11-01T09:00:00+00:00','Declined Lately','MP for Leeds South','/b/briefingab',NULL,'This is about another member entirely.',NULL,'office@example.org','a1a1a1a1a1a1a1a1a1a1a1a1','declined',NULL,NULL,'2026-11-02T09:00:00+00:00','It is about another member''s conduct.',NULL);
INSERT INTO "statements" VALUES(4,'2026-07-01T09:00:00+00:00','Withdrawn Long Ago','MP for Leeds South','/people/10669',10669,'Shown for a while, then not.',NULL,NULL,NULL,'withdrawn','2026-07-02T09:00:00+00:00',NULL,'2026-08-01T10:00:00+00:00','Withdrawn by the author.','Telephoned the office.');
INSERT INTO "statements" VALUES(5,'2026-07-05T09:00:00+00:00','Taken Down Lately','MP for Leeds South','/b/briefingab',10115,'Shown until last week, when it was taken down.',NULL,'office@example.org','a1a1a1a1a1a1a1a1a1a1a1a1','declined','2026-07-06T09:00:00+00:00',NULL,'2026-11-06T09:00:00+00:00','It broke the rules: it named a private person.','Replied from their own address.');
INSERT INTO "statements" VALUES(6,'2026-06-01T09:00:00+00:00','Declined Long Ago','MP for Leeds South','/debates/2021-03-04/commons/trial-by-jury',NULL,'Never shown, and declined in June.',NULL,NULL,NULL,'declined',NULL,NULL,'2026-06-02T09:00:00+00:00','Not from the member.',NULL);
INSERT INTO "statements" VALUES(7,'2026-07-01T09:00:00+00:00','Waiting Too Long','MP for Leeds South','/b/briefingac',NULL,'Nobody decided this one in four months.',NULL,'waiting@example.org','a1a1a1a1a1a1a1a1a1a1a1a1','pending',NULL,NULL,NULL,NULL,NULL);
INSERT INTO "statements" VALUES(8,'2026-11-11T09:00:00+00:00','Shown With No Link','MP for Leeds South','/people/10669',10669,'Published as written, no link.',NULL,'office@example.org','a1a1a1a1a1a1a1a1a1a1a1a1','published','2026-11-11T15:00:00+00:00',NULL,NULL,NULL,'Replied from their parliament.uk address.');
CREATE TABLE sync_log (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    stats       TEXT,
    error       TEXT
);
INSERT INTO "sync_log" VALUES(1,'sync','2026-11-13T11:40:00+00:00','2026-11-13T11:40:00+00:00','ok','{"files": 3}',NULL);
CREATE TABLE topic_checks (
    slug        TEXT PRIMARY KEY,
    checked_at  TEXT NOT NULL,
    outcome     TEXT NOT NULL
);
CREATE TABLE topic_versions (
    slug          TEXT NOT NULL,
    briefing_id   TEXT NOT NULL,
    prepared_at   TEXT NOT NULL,
    records_up_to TEXT,
    PRIMARY KEY (slug, briefing_id)
);
INSERT INTO "topic_versions" VALUES('trial-by-jury','briefingad','2026-11-03T07:15:20+00:00','2026-11-02');
CREATE TABLE usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,
    model         TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0
, visitor TEXT);
INSERT INTO "usage" VALUES(1,NULL,'2026-10-20T08:00:00+00:00','answer','claude-sonnet-5-5',9000,700,3000,2000,0.0266,'0f0f0f0f0f0f0f0f0f0f0f0f');
INSERT INTO "usage" VALUES(2,NULL,'2026-11-02T08:00:00+00:00','parse','claude-haiku-5-5',1200,80,0,0,0.00016,'1e1e1e1e1e1e1e1e1e1e1e1e');
INSERT INTO "usage" VALUES(3,NULL,'2026-11-02T08:00:09+00:00','answer','claude-sonnet-5-5',123457,15999,77777,3333,0.424126,'1e1e1e1e1e1e1e1e1e1e1e1e');
INSERT INTO "usage" VALUES(4,NULL,'2026-11-02T08:00:09+00:00','interests','claude-haiku-5-5',400,30,0,0,5.5e-05,'1e1e1e1e1e1e1e1e1e1e1e1e');
INSERT INTO "usage" VALUES(5,NULL,'2026-11-03T07:15:00+00:00','topic','claude-haiku-5-5',1200,80,0,0,0.00016,NULL);
INSERT INTO "usage" VALUES(6,NULL,'2026-11-03T07:15:20+00:00','topic','claude-sonnet-5-5',9000,700,3000,2000,0.0266,NULL);
INSERT INTO "usage" VALUES(7,NULL,'2026-11-13T00:00:00+00:00','parse','claude-haiku-5-5',1200,80,0,0,0.00016,'a1a1a1a1a1a1a1a1a1a1a1a1');
INSERT INTO "usage" VALUES(8,NULL,'2026-11-13T00:00:07+00:00','answer','claude-sonnet-5-5',9000,700,3000,2000,0.0266,'a1a1a1a1a1a1a1a1a1a1a1a1');
INSERT INTO "usage" VALUES(9,NULL,'2026-11-13T09:30:00+00:00','answer','claude-sonnet-5-5',1000,200,0,0,0.004,'a1a1a1a1a1a1a1a1a1a1a1a1');
INSERT INTO "usage" VALUES(10,NULL,'2026-11-13T10:00:00+00:00','answer','claude-sonnet-5-5',9000,700,3000,2000,0.0266,'b2b2b2b2b2b2b2b2b2b2b2b2');
INSERT INTO "usage" VALUES(11,1,'2026-11-13T10:00:01+00:00','answer','claude-opus-5-5',1000,200,0,0,0.008,NULL);
INSERT INTO "usage" VALUES(12,1,'2026-11-13T10:00:02+00:00','answer',NULL,0,0,0,0,0.0,NULL);
INSERT INTO "usage" VALUES(13,99,'2026-11-13T10:30:00+00:00','answer','claude-sonnet-5-5',1000,200,0,0,0.004,NULL);
CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "users" VALUES(1,'researcher@example.org','$argon2id$not-a-real-hash$1',0,'2026-10-01T09:00:00+00:00',0);
INSERT INTO "users" VALUES(2,'admin@example.org','$argon2id$not-a-real-hash$2',1,'2026-10-01T09:00:00+00:00',0);
CREATE INDEX usage_user_at ON usage(user_id, at);
CREATE INDEX briefings_created ON briefings(created_at);
CREATE INDEX reports_status ON reports(status, at);
CREATE INDEX statements_item ON statements(status, item_path);
CREATE INDEX statements_person ON statements(status, person);
CREATE INDEX usage_visitor_at ON usage(visitor, at);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',2);
INSERT INTO "sqlite_sequence" VALUES('usage',13);
INSERT INTO "sqlite_sequence" VALUES('failures',5);
INSERT INTO "sqlite_sequence" VALUES('reports',6);
INSERT INTO "sqlite_sequence" VALUES('statements',8);
INSERT INTO "sqlite_sequence" VALUES('feedback',2);
INSERT INTO "sqlite_sequence" VALUES('sync_log',1);
COMMIT;

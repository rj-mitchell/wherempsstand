"""Raw Hansard files stored gzipped (owner's decision 14): compressing the mirror parses nothing again, while
corrections and new days still arrive."""

import dataclasses
import gzip
import os
import time
from datetime import UTC, date, datetime

import httpx
import pytest

from hansard.ingest import ingest
from hansard.parse.xml_stream import parse_divisions, parse_file
from hansard.sources.twfy import (
    HttpsMirror,
    compress_mirror,
    content_stat,
    listed_mtime_matches,
    local_files,
    stored_path,
    write_gzip,
)
from hansard.store.state import StateDB

SINCE = date(2010, 1, 1)
BASE = "https://example.org/scrapedxml"


def _all_plain(root):
    return sorted(root.glob("*/*.xml"))


def test_parser_reads_gzip_the_same_as_plain(xml_mirror, tmp_path):
    for p in _all_plain(xml_mirror):
        gz = tmp_path / p.parent.name / (p.name + ".gz")
        gz.parent.mkdir(exist_ok=True)
        write_gzip(gz, p.read_bytes())
        if p.parent.name.endswith("divisionsonly"):
            assert parse_divisions(gz, p.parent.name) == parse_divisions(p, p.parent.name)
        else:
            assert list(parse_file(gz, p.parent.name)) == list(parse_file(p, p.parent.name))


def test_content_stat_gives_the_xml_size_and_kept_mtime(tmp_path):
    plain = tmp_path / "debates" / "debates2026-01-01a.xml"
    plain.parent.mkdir()
    plain.write_bytes(b"<publicwhip>" + b"x" * 5000 + b"</publicwhip>")
    os.utime(plain, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
    gz = plain.with_name(plain.name + ".gz")
    write_gzip(gz, plain.read_bytes(), mtime_ns=plain.stat().st_mtime_ns)
    assert content_stat(gz) == content_stat(plain)
    assert gz.stat().st_size < plain.stat().st_size


def test_compressing_an_ingested_mirror_parses_nothing_again(settings, xml_mirror):
    first = ingest(settings, workers=1, since=SINCE)
    assert first.errors == [] and first.files_parsed == first.files_seen > 0 and first.divisions > 0
    manifest = {k: dict(v) for k, v in StateDB(settings.state_db).manifest().items()}
    mtimes = {p: p.stat().st_mtime_ns for p in _all_plain(xml_mirror)}

    out = compress_mirror(xml_mirror)
    assert out["compressed"] == len(mtimes) and out["plain_files_left"] == 0 and out["conflicts"] == []
    assert out["gb_after"] <= out["gb_before"]
    for p, mtime_ns in mtimes.items():
        gz = p.with_name(p.name + ".gz")
        assert not p.exists() and gz.stat().st_mtime_ns == mtime_ns

    again = ingest(settings, workers=1, since=SINCE)
    assert again.files_seen == first.files_seen
    assert (again.files_parsed, again.files_new, again.files_revised, again.divisions) == (0, 0, 0, 0)
    assert {k: dict(v) for k, v in StateDB(settings.state_db).manifest().items()} == manifest


def test_compress_raw_is_resumable(settings, xml_mirror):
    ingest(settings, workers=1, since=SINCE)
    plain = _all_plain(xml_mirror)
    out = compress_mirror(xml_mirror, limit=3)
    assert out["compressed"] == 3 and out["plain_files_left"] == len(plain) - 3

    # Stopped mid-way: once between writing a .gz and deleting the plain file, once while writing a .part.
    a, b = _all_plain(xml_mirror)[:2]
    write_gzip(a.with_name(a.name + ".gz"), a.read_bytes(), mtime_ns=a.stat().st_mtime_ns)
    b.with_name(b.name + ".gz.part").write_bytes(b"half a file")
    assert ingest(settings, workers=1, since=SINCE).files_parsed == 0  # a file in both forms: the .gz is read

    out = compress_mirror(xml_mirror)
    assert out["duplicates_removed"] == 1 and out["compressed"] == len(plain) - 4
    assert out["plain_files_left"] == 0 and not list(xml_mirror.glob("*/*.part"))
    assert ingest(settings, workers=1, since=SINCE).files_parsed == 0


def test_dry_run_changes_nothing(xml_mirror):
    before = {p: p.stat().st_mtime_ns for p in xml_mirror.rglob("*")}
    out = compress_mirror(xml_mirror, dry_run=True)
    assert out["would_compress"] == out["plain_files"] > 0 and 0 < out["sample_ratio"] < 0.5
    assert {p: p.stat().st_mtime_ns for p in xml_mirror.rglob("*")} == before


# --------------------------------------------------------------------------- the HTTPS mirror, stored gzipped


class FakeTWFY:
    """A scrapedxml directory listing and its files, in the format TheyWorkForYou's server gives."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}  # rel path -> (content, listed mtime)
        self.fetched: list[str] = []

    def put(self, rel: str, content: bytes, mtime: str) -> None:
        self.files[rel] = (content, mtime)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/scrapedxml/")
        if path.endswith("/"):
            rows = "".join(f'<tr><td><a href="{rel.split("/")[1]}">{rel.split("/")[1]}</a></td>'
                           f'<td align="right">{m}  </td><td align="right"> {len(c) // 1024}K</td></tr>\n'
                           for rel, (c, m) in sorted(self.files.items()) if rel.startswith(path))
            return httpx.Response(200, text=f"<table>{rows}</table>")
        if path not in self.files:
            return httpx.Response(404)
        self.fetched.append(path)
        return httpx.Response(200, content=self.files[path][0])

    def mirror(self, dest, **kw) -> HttpsMirror:
        m = HttpsMirror(BASE, dest, **kw)
        m.client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return m


@pytest.fixture()
def twfy(xml_mirror):
    """TheyWorkForYou serving the fixture files, each listed as last changed at 06:13 on 1 March 2026."""
    fake = FakeTWFY()
    for p in _all_plain(xml_mirror):
        fake.put(f"{p.parent.name}/{p.name}", p.read_bytes(), "2026-03-01 06:13")
    return fake


def _sync(settings, twfy, dirs):
    m = twfy.mirror(settings.raw_xml_dir)
    done = m.download(m.plan(dirs, SINCE))
    return m, done, ingest(settings, workers=1, since=SINCE, directories=dirs)


def test_a_new_day_is_stored_gzipped_and_ingested(settings, twfy):
    import shutil

    shutil.rmtree(settings.raw_xml_dir)
    m, done, stats = _sync(settings, twfy, ["debates", "wrans"])
    assert len(done) == 4 and stats.files_new == 4 and stats.errors == []
    d = settings.raw_xml_dir / "debates"
    assert sorted(p.name for p in d.iterdir()) == [
        "debates2012-03-07a.xml.gz", "debates2012-03-07a.xml.mtime",
        "debates2026-09-15c.xml.gz", "debates2026-09-15c.xml.mtime"]
    assert (d / "debates2026-09-15c.xml.mtime").read_text() == "2026-03-01 06:13"

    # nothing new: nothing downloaded or parsed
    m, done, stats = _sync(settings, twfy, ["debates", "wrans"])
    assert done == [] and stats.files_parsed == 0

    # a new sitting day
    text = twfy.files["debates/debates2026-09-15c.xml"][0].replace(b"2026-09-15c.", b"2026-09-16a.")
    twfy.put("debates/debates2026-09-16a.xml", text, "2026-09-17 06:14")
    m, done, stats = _sync(settings, twfy, ["debates", "wrans"])
    assert [f.name for f in done] == ["debates2026-09-16a.xml"]
    assert stats.files_new == 1 and stats.files_parsed == 1 and stats.records > 0
    assert (d / "debates2026-09-16a.xml.gz").exists() and not (d / "debates2026-09-16a.xml").exists()


def test_corrections_still_arrive_after_compressing(settings, xml_mirror, twfy):
    dirs = ["debates", "wrans"]
    # The mirror as rsync left it: plain files, no markers, each with TheyWorkForYou's mtime (06:13 GMT).
    listed = datetime(2026, 3, 1, 6, 13, 41, tzinfo=UTC).timestamp()
    for p in _all_plain(xml_mirror):
        os.utime(p, (listed, listed))
    ingest(settings, workers=1, since=SINCE, directories=dirs)
    compress_mirror(xml_mirror)

    # The first HTTPS sync takes the markers from the listing rather than downloading everything again.
    m, done, stats = _sync(settings, twfy, dirs)
    assert done == [] and twfy.fetched == [] and m.adopted == 4 and stats.files_parsed == 0

    # A corrected revision "d" of a sitting day.
    text = twfy.files["debates/debates2026-09-15c.xml"][0]
    twfy.put("debates/debates2026-09-15d.xml",
             text.replace(b"2026-09-15c.", b"2026-09-15d.").replace(b"Trial by Jury", b"Trial by Jury (corrected)"),
             "2026-09-20 06:13")
    # An unlettered written-answers file rewritten in place: the same name, a new listed time.
    wrans = twfy.files["wrans/answers2026-10-08.xml"][0]
    twfy.put("wrans/answers2026-10-08.xml", wrans.replace(b"Freedom of Information", b"FOI"), "2026-10-10 06:15")
    m, done, stats = _sync(settings, twfy, dirs)
    assert sorted(f.name for f in done) == ["answers2026-10-08.xml", "debates2026-09-15d.xml"]
    assert stats.files_revised == 2 and stats.files_parsed == 2 and stats.errors == []
    manifest = StateDB(settings.state_db).manifest()
    assert manifest[("debates", "debates2026-09-15")]["revision"] == "d"
    assert b"FOI" in gzip.decompress((xml_mirror / "wrans" / "answers2026-10-08.xml.gz").read_bytes())

    # TheyWorkForYou touches a file without changing it: downloaded, but kept as it is, so not parsed again.
    twfy.put("debates/debates2012-03-07a.xml", twfy.files["debates/debates2012-03-07a.xml"][0], "2026-11-01 06:13")
    m, done, stats = _sync(settings, twfy, dirs)
    assert [f.name for f in done] == ["debates2012-03-07a.xml"] and m.unchanged == 1 and stats.files_parsed == 0


def test_rsync_files_with_another_time_are_downloaded_again(settings, xml_mirror, twfy):
    for p in _all_plain(xml_mirror):
        os.utime(p, (time.time(), time.time()))  # not TheyWorkForYou's time: the marker can't be taken on trust
    m, done, stats = _sync(settings, twfy, ["wms"])
    assert m.adopted == 0 and len(done) == 1 and m.unchanged == 1
    assert sorted(p.name for p in (xml_mirror / "wms").iterdir()) == [
        "ministerial2026-09-15.xml.gz", "ministerial2026-09-15.xml.mtime"]


def test_plain_mirror_when_compression_is_off(settings, twfy):
    import shutil

    shutil.rmtree(settings.raw_xml_dir)
    m = twfy.mirror(settings.raw_xml_dir, compress=False)
    m.download(m.plan(["wms"], SINCE))
    assert sorted(p.name for p in (settings.raw_xml_dir / "wms").iterdir()) == [
        "ministerial2026-09-15.xml", "ministerial2026-09-15.xml.mtime"]


def test_auto_transport_is_https_when_storing_gzipped(settings, monkeypatch):
    from hansard import cli
    from hansard.sources import twfy

    calls = []
    monkeypatch.setattr(twfy, "rsync_available", lambda source: True)
    monkeypatch.setattr(twfy, "rsync_directories", lambda *a: calls.append("rsync"))
    monkeypatch.setattr(twfy.HttpsMirror, "plan", lambda self, dirs, since: calls.append("https") or [])
    assert cli.fetch_xml(settings)["transport"] == "https" and calls == ["https"]

    off = dataclasses.replace(settings, raw={**settings.raw,
                                             "sources": {**settings.sources, "compress_raw_xml": False}})
    calls.clear()
    assert cli.fetch_xml(off)["transport"] == "rsync" and calls == ["rsync"]


def test_listed_time_is_uk_time():
    gmt = datetime(2025, 12, 16, 6, 14, 16, tzinfo=UTC).timestamp()   # listed as 06:14 in December
    bst = datetime(2025, 7, 16, 5, 13, 55, tzinfo=UTC).timestamp()    # listed as 06:13 in July (BST)
    assert listed_mtime_matches("2025-12-16 06:14", gmt)
    assert listed_mtime_matches("2025-07-16 06:13", bst)
    assert not listed_mtime_matches("2025-07-16 06:13", bst + 120)
    assert not listed_mtime_matches("", gmt)


def test_stored_path_prefers_gzip(tmp_path):
    (tmp_path / "wms").mkdir()
    (tmp_path / "wms" / "ministerial2026-09-15.xml").write_text("<a/>")
    assert stored_path(tmp_path, "wms/ministerial2026-09-15.xml").name == "ministerial2026-09-15.xml"
    write_gzip(tmp_path / "wms" / "ministerial2026-09-15.xml.gz", b"<a/>")
    assert stored_path(tmp_path, "wms/ministerial2026-09-15.xml").name == "ministerial2026-09-15.xml.gz"
    assert [f.name for f in local_files(tmp_path, ["wms"], SINCE)] == ["ministerial2026-09-15.xml"]

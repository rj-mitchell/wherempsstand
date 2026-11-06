"""Raw Hansard files stored gzipped (owner's decision 14): compressing the mirror parses nothing again, while
corrections and new days still arrive."""

import os
from datetime import date

from hansard.parse.xml_stream import parse_divisions, parse_file
from hansard.sources.twfy import (
    content_stat,
    local_files,
    stored_path,
    write_gzip,
)

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


def test_stored_path_prefers_gzip(tmp_path):
    (tmp_path / "wms").mkdir()
    (tmp_path / "wms" / "ministerial2026-09-15.xml").write_text("<a/>")
    assert stored_path(tmp_path, "wms/ministerial2026-09-15.xml").name == "ministerial2026-09-15.xml"
    write_gzip(tmp_path / "wms" / "ministerial2026-09-15.xml.gz", b"<a/>")
    assert stored_path(tmp_path, "wms/ministerial2026-09-15.xml").name == "ministerial2026-09-15.xml.gz"
    assert [f.name for f in local_files(tmp_path, ["wms"], SINCE)] == ["ministerial2026-09-15.xml"]

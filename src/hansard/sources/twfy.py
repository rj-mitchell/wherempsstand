"""Mirror TheyWorkForYou / ParlParse scrapedxml files.

Two transports:

* **rsync** from ``data.theyworkforyou.com::parldata`` (TheyWorkForYou's
  recommended method; used on the server when port 873 is reachable);
* **HTTPS** from ``https://www.theyworkforyou.com/pwdata/scrapedxml`` (works
  through restrictive proxies). Directory listings give each file's size and
  modification time, so only new or changed files are downloaded.

File naming
-----------
Day files carry a revision letter that is bumped when Hansard is corrected:
``debates2025-12-18a.xml`` .. ``debates2025-12-18d.xml``. Earlier revisions are
superseded (they may contain only ``<gidredirect>`` elements). Some newer
written-answer and statement files have no letter and are rewritten in place,
which the size/mtime comparison catches. Only the latest revision of each
*key* (file name without the revision letter) is kept.

Compressed storage
------------------
The HTTPS mirror stores each file gzipped, as ``<name>.xml.gz`` (about a fifth
of the size), next to the usual ``<name>.xml.mtime`` marker. rsync can only
write plain ``.xml``, so it is used only when ``[sources] compress_raw_xml`` is
false. Everything that reads the mirror accepts both forms; where a file is
present in both, the ``.gz`` is read. ``content_stat`` gives the size of the
XML inside, so compressing a file in place (keeping its mtime) does not look
like a change to the ingest manifest. ``hansard compress-raw`` compresses an
existing plain mirror once.
"""

from __future__ import annotations

import gzip
import html
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO

import httpx

log = logging.getLogger(__name__)

# directory -> (house, record family). Order matters for the CLI listing only.
SOURCES: dict[str, tuple[str, str]] = {
    "debates": ("commons", "debate"),
    "westminhall": ("commons", "westminster_hall"),
    "lordspages": ("lords", "debate"),
    "wrans": ("commons", "written_answer"),
    "lordswrans": ("lords", "written_answer"),
    "wms": ("commons", "written_statement"),
    "lordswms": ("lords", "written_statement"),
    "standing": ("commons", "public_bill_committee"),
    "divisionsonly": ("commons", "division"),
    "lordsdivisionsonly": ("lords", "division"),
}

_NAME_RE = re.compile(r"^(?P<stem>.*?(?P<date>\d{4}-\d{2}-\d{2}))(?P<rev>[a-z]?)\.xml$")
_LISTING_RE = re.compile(
    r'href="(?P<name>[^"/]+\.xml)".*?</td><td[^>]*>\s*(?P<mtime>\d{4}-\d\d-\d\d \d\d:\d\d)\s*</td>'
    r'<td[^>]*>\s*(?P<size>[\d.]+[KMG]?)\s*<', re.S)


@dataclass(frozen=True)
class RemoteFile:
    directory: str
    name: str
    key: str          # file name without revision letter, e.g. "debates2025-12-18"
    revision: str     # "" for unlettered files
    file_date: date
    size_hint: str = ""
    mtime: str = ""

    @property
    def rel_path(self) -> str:
        return f"{self.directory}/{self.name}"


GZ = ".gz"
GZIP_LEVEL = 6


def xml_name(name: str) -> str:
    """The mirrored file's own name: ``debates2025-12-18d.xml.gz`` -> ``debates2025-12-18d.xml``."""
    return name[:-len(GZ)] if name.endswith(GZ) else name


def stored_path(root: Path, rel_path: str) -> Path:
    """The file on disk for a mirrored ``.xml`` name: its gzipped copy if there is one, otherwise the plain file."""
    plain = root / rel_path
    gz = plain.with_name(plain.name + GZ)
    return gz if gz.exists() else plain


def marker_path(root: Path, rel_path: str) -> Path:
    """The ``.mtime`` marker (TheyWorkForYou's listed modification time), named after the plain file whichever form
    is stored."""
    plain = root / rel_path
    return plain.with_name(plain.name + ".mtime")


def content_stat(path: Path) -> tuple[int, int]:
    """(size of the XML, mtime in ns). For a gzipped file the size is the uncompressed size, from the gzip trailer,
    so a file compressed in place with its mtime kept has the same (size, mtime) as before."""
    st = path.stat()
    if not path.name.endswith(GZ):
        return st.st_size, st.st_mtime_ns
    with open(path, "rb") as fh:
        fh.seek(-4, os.SEEK_END)
        return int.from_bytes(fh.read(4), "little"), st.st_mtime_ns


def open_xml(path: Path) -> BinaryIO:
    """The XML of a mirrored file, plain or gzipped, as a binary stream."""
    return gzip.open(path, "rb") if path.name.endswith(GZ) else open(path, "rb")  # type: ignore[return-value]


def read_xml(path: Path) -> bytes:
    with open_xml(path) as fh:
        return fh.read()


def write_gzip(dest: Path, data: bytes, *, mtime_ns: int | None = None) -> None:
    """Write ``data`` gzipped to ``dest`` (a ``.xml.gz`` path) safely: to a ``.part`` file first, flushed to disk and
    read back to check it decompresses to exactly ``data``, then renamed into place. With ``mtime_ns``, the file
    keeps that modification time (the original's, when compressing a file in place)."""
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename=xml_name(dest.name), mode="wb", compresslevel=GZIP_LEVEL, fileobj=raw,
                           mtime=(mtime_ns or 0) // 1_000_000_000) as gz:
            gz.write(data)
        raw.flush()
        os.fsync(raw.fileno())
    if gzip.decompress(tmp.read_bytes()) != data:
        tmp.unlink()
        raise OSError(f"{dest.name}: the compressed copy did not read back the same")
    if mtime_ns is not None:
        os.utime(tmp, ns=(mtime_ns, mtime_ns))
    tmp.replace(dest)


def parse_name(directory: str, name: str, size_hint: str = "", mtime: str = "") -> RemoteFile | None:
    m = _NAME_RE.match(name)
    if not m:
        return None
    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    return RemoteFile(directory, name, m.group("stem"), m.group("rev"), d, size_hint, mtime)


def latest_revisions(files: Iterable[RemoteFile]) -> list[RemoteFile]:
    """Keep only the highest revision letter for each key."""
    best: dict[tuple[str, str], RemoteFile] = {}
    for f in files:
        k = (f.directory, f.key)
        if k not in best or f.revision > best[k].revision:
            best[k] = f
    return sorted(best.values(), key=lambda f: (f.directory, f.file_date, f.key))


def parse_listing(directory: str, page: str) -> list[RemoteFile]:
    out = []
    for m in _LISTING_RE.finditer(page):
        rf = parse_name(directory, html.unescape(m.group("name")), m.group("size"), m.group("mtime"))
        if rf:
            out.append(rf)
    return out


class HttpsMirror:
    def __init__(self, base_url: str, dest: Path, *, concurrency: int = 4, timeout: float = 60.0,
                 user_agent: str = "WhereMPsStand (open-source research tool; +https://github.com/rj-mitchell/wherempsstand)"):
        self.base_url = base_url.rstrip("/")
        self.dest = dest
        self.concurrency = concurrency
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True)

    def list_directory(self, directory: str) -> list[RemoteFile]:
        resp = self._get(f"{self.base_url}/{directory}/")
        return parse_listing(directory, resp.text)

    def plan(self, directories: Iterable[str], since: date, until: date | None = None) -> list[RemoteFile]:
        wanted: list[RemoteFile] = []
        for d in directories:
            files = [f for f in self.list_directory(d) if f.file_date >= since and (until is None or f.file_date <= until)]
            wanted.extend(latest_revisions(files))
        return wanted

    def needs_download(self, f: RemoteFile) -> bool:
        local = self.dest / f.rel_path
        if not local.exists():
            return True
        marker = local.with_suffix(local.suffix + ".mtime")
        return not marker.exists() or marker.read_text().strip() != f.mtime

    def download(self, files: list[RemoteFile], progress_every: int = 500) -> list[RemoteFile]:
        todo = [f for f in files if self.needs_download(f)]
        done: list[RemoteFile] = []
        if not todo:
            return done
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for i, f in enumerate(pool.map(self._fetch_one, todo), 1):
                if f is not None:
                    done.append(f)
                if i % progress_every == 0:
                    log.info("downloaded %d/%d files", i, len(todo))
        return done

    def _fetch_one(self, f: RemoteFile) -> RemoteFile | None:
        local = self.dest / f.rel_path
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_suffix(".part")
        try:
            resp = self._get(f"{self.base_url}/{f.rel_path}")
        except httpx.HTTPError as exc:
            log.warning("failed to fetch %s: %s", f.rel_path, exc)
            return None
        tmp.write_bytes(resp.content)
        tmp.replace(local)
        local.with_suffix(local.suffix + ".mtime").write_text(f.mtime)
        return f

    def _get(self, url: str, attempts: int = 4) -> httpx.Response:
        delay = 2.0
        for attempt in range(attempts):
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                return resp
            except httpx.HTTPError:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")


def rsync_available(source: str) -> bool:
    if not shutil.which("rsync"):
        return False
    try:
        subprocess.run(["rsync", "--list-only", f"{source}/"], check=True, capture_output=True, timeout=30)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def rsync_directories(source: str, dest: Path, directories: Iterable[str], since_year: int) -> None:
    """rsync the given directories, limited to files from ``since_year`` onwards."""
    years = range(since_year, datetime.now().year + 1)
    for d in directories:
        target = dest / d
        target.mkdir(parents=True, exist_ok=True)
        includes = [arg for y in years for arg in ("--include", f"*{y}-*.xml")]
        cmd = ["rsync", "-az", "--delete-after", "--exclude", ".svn", "--exclude", "tmp/",
               *includes, "--exclude", "*", f"{source}/{d}/", f"{target}/"]
        log.info("rsync %s", d)
        subprocess.run(cmd, check=True)


def local_files(root: Path, directories: Iterable[str], since: date) -> list[RemoteFile]:
    """Latest local revision of each key under ``root`` (a scrapedxml mirror), stored plain or gzipped. Names are
    always the plain ``.xml`` name; ``stored_path`` finds the file on disk."""
    found: set[RemoteFile] = set()
    for d in directories:
        base = root / d
        if not base.exists():
            continue
        for p in [*base.glob("*.xml"), *base.glob("*.xml.gz")]:
            rf = parse_name(d, xml_name(p.name))
            if rf and rf.file_date >= since:
                found.add(rf)
    return latest_revisions(found)


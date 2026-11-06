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
The HTTPS mirror stores each file gzipped, as ``<name>.xml.gz`` (about a quarter
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
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
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


def listed_mtime_matches(listed: str, mtime: float) -> bool:
    """Whether a local file's mtime is the time TheyWorkForYou's listing gives for it. The listing is to the minute
    in UK time; rsync keeps the source's own mtime, so a file it mirrored matches to the minute, as GMT or BST."""
    try:
        t = int(datetime.strptime(listed, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp())
    except ValueError:
        return False
    return int(mtime) // 60 * 60 in (t, t - 3600)


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
    def __init__(self, base_url: str, dest: Path, *, concurrency: int = 4, timeout: float = 60.0, compress: bool = True,
                 user_agent: str = "WhereMPsStand (open-source research tool; +https://github.com/rj-mitchell/wherempsstand)"):
        self.base_url = base_url.rstrip("/")
        self.dest = dest
        self.concurrency = concurrency
        self.compress = compress
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": user_agent}, follow_redirects=True)
        self.adopted = 0     # files mirrored by rsync whose marker was written from the listing, not downloaded
        self.unchanged = 0   # downloaded, but the same as the copy already here (kept, with its mtime)
        self._lock = threading.Lock()

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
        if not stored_path(self.dest, f.rel_path).exists():
            return True
        marker = marker_path(self.dest, f.rel_path)
        return not marker.exists() or marker.read_text().strip() != f.mtime

    def adopt(self, f: RemoteFile) -> bool:
        """A file mirrored by rsync has no marker, but rsync kept TheyWorkForYou's modification time on it. If that is
        the time in the listing, write the marker instead of downloading the file again."""
        marker = marker_path(self.dest, f.rel_path)
        local = stored_path(self.dest, f.rel_path)
        if marker.exists() or not local.exists() or not listed_mtime_matches(f.mtime, local.stat().st_mtime):
            return False
        marker.write_text(f.mtime)
        return True

    def download(self, files: list[RemoteFile], progress_every: int = 500) -> list[RemoteFile]:
        self.adopted += sum(self.adopt(f) for f in files)
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
        plain = self.dest / f.rel_path
        gz = plain.with_name(plain.name + GZ)
        plain.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = self._get(f"{self.base_url}/{f.rel_path}")
        except httpx.HTTPError as exc:
            log.warning("failed to fetch %s: %s", f.rel_path, exc)
            return None
        data = resp.content
        local = stored_path(self.dest, f.rel_path)
        if local.exists() and read_xml(local) == data:
            # The same as the copy here: keep it and its mtime, so it is not parsed again for nothing.
            if self.compress and local == plain:
                write_gzip(gz, data, mtime_ns=plain.stat().st_mtime_ns)
                plain.unlink()
            with self._lock:
                self.unchanged += 1
        elif self.compress:
            write_gzip(gz, data)
            plain.unlink(missing_ok=True)
        else:
            tmp = plain.with_name(plain.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(plain)
            gz.unlink(missing_ok=True)
        marker_path(self.dest, f.rel_path).write_text(f.mtime)
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


def drop_shadowed_gzip(root: Path, directories: Iterable[str]) -> int:
    """After an rsync (which writes plain files), remove any gzipped copy of a file rsync has just written, so the
    plain mirror is the one read. Returns how many were removed."""
    n = 0
    for d in directories:
        for gz in (root / d).glob("*.xml.gz"):
            if gz.with_name(xml_name(gz.name)).exists():
                gz.unlink()
                n += 1
    return n


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


def _gb(n: int) -> float:
    return round(n / 1e9, 2)


def _mirror_bytes(root: Path) -> int:
    return sum(p.stat().st_size for pattern in ("*/*.xml", "*/*.xml.gz") for p in root.glob(pattern))


def superseded(root: Path) -> tuple[int, int]:
    """(files, bytes) of earlier revisions kept beside a later one (rsync mirrors every revision; only the latest is
    read)."""
    by_key: dict[tuple[str, str], list[tuple[str, Path]]] = {}
    for p in [*root.glob("*/*.xml"), *root.glob("*/*.xml.gz")]:
        rf = parse_name(p.parent.name, xml_name(p.name))
        if rf:
            by_key.setdefault((rf.directory, rf.key), []).append((rf.revision, p))
    old = [p for revs in by_key.values() for rev, p in revs if rev < max(r for r, _ in revs)]
    return len(old), sum(p.stat().st_size for p in old)


def compress_mirror(root: Path, *, dry_run: bool = False, limit: int | None = None,
                    sample: int = 40) -> dict[str, object]:
    """Gzip every plain ``.xml`` in the mirror, once (``hansard compress-raw``).

    Each file is compressed to a ``.part`` file, read back and checked, given the original's mtime, renamed to
    ``<name>.xml.gz``, and only then is the plain file deleted. The ingest manifest compares the XML's own size and
    the mtime, both unchanged, so nothing is parsed again; markers are named after the plain file, so they still
    apply. Safe to stop at any point and run again: a leftover ``.part`` is deleted, and a file found in both forms
    has its plain copy deleted once the ``.gz`` is checked to hold the same XML.
    """
    started = time.time()
    if not dry_run:
        for part in root.glob("*/*.xml.gz.part"):
            part.unlink()
    before = _mirror_bytes(root)
    plain = sorted(root.glob("*/*.xml"))
    plain_bytes = sum(p.stat().st_size for p in plain)
    old_files, old_bytes = superseded(root)
    out: dict[str, object] = {"dry_run": dry_run, "plain_files": len(plain), "plain_gb": _gb(plain_bytes),
                              "gb_before": _gb(before), "superseded_revisions": old_files,
                              "superseded_gb": _gb(old_bytes)}
    if dry_run:
        step = max(1, len(plain) // sample) if plain else 1
        picked = plain[::step][:sample]
        raw = sum(p.stat().st_size for p in picked)
        packed = sum(len(gzip.compress(p.read_bytes(), compresslevel=GZIP_LEVEL)) for p in picked)
        ratio = packed / raw if raw else 1.0
        out.update({"sample_files": len(picked), "sample_ratio": round(ratio, 3),
                    "estimated_gb_after": _gb(before - plain_bytes + int(plain_bytes * ratio)),
                    "would_compress": len(plain[:limit] if limit else plain)})
        return out
    compressed = resolved = 0
    conflicts: list[str] = []
    for i, p in enumerate(plain[:limit] if limit else plain, 1):
        gz = p.with_name(p.name + GZ)
        data = p.read_bytes()
        mtime_ns = p.stat().st_mtime_ns
        if gz.exists():
            if read_xml(gz) == data:  # an earlier run stopped between writing the .gz and deleting the plain file
                p.unlink()
                resolved += 1
            else:  # not written by this command or the HTTPS mirror; left for a person to look at (the .gz is read)
                conflicts.append(f"{p.parent.name}/{p.name}")
            continue
        write_gzip(gz, data, mtime_ns=mtime_ns)
        if gz.stat().st_mtime_ns != mtime_ns:
            raise OSError(f"{gz}: the modification time was not kept; stopping before deleting the original")
        p.unlink()
        compressed += 1
        if i % 2000 == 0:
            log.info("compressed %d of %d files", i, len(plain))
    after = _mirror_bytes(root)
    out.update({"compressed": compressed, "duplicates_removed": resolved, "conflicts": conflicts,
                "plain_files_left": len(list(root.glob("*/*.xml"))), "gb_after": _gb(after),
                "gb_freed": _gb(before - after), "seconds": round(time.time() - started, 1)})
    return out

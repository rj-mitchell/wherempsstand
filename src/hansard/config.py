"""The site's settings: config/settings.toml, read by the Where They Stand sites' shared loader (wts-core,
``wts_core.settings``), and the facts about this site that are fixed in its code.

The loader reads config/settings.toml (or the file named by ``HANSARD_CONFIG``), then ``settings.local.toml`` in
the data folder if there is one (laid over it one level deep; written by ``hansard bootstrap`` and
``hansard reindex-vectors``, or by hand on a server), then any overrides given by name
(``**{"usage.daily_spend_cap_usd": 1.0}``). The data folder is ``HANSARD_DATA_DIR``, else ``[paths] data_dir``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

from wts_core import settings as core

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "settings.toml"
STATIC_DIR = REPO_ROOT / "data_static"


def _version() -> str:
    try:
        return metadata.version("hansard")
    except metadata.PackageNotFoundError:  # run from the source without being installed
        return "0"


SITE = core.Site(
    name="Where MPs Stand",
    package="hansard",
    env_prefix="HANSARD",  # HANSARD_DATA_DIR, HANSARD_CONFIG, HANSARD_ANTHROPIC_API_KEY, HANSARD_B2_BUCKET…
    root=REPO_ROOT,
    hosts=("wherempsstand.com", "www.wherempsstand.com"),
    state_file="state.sqlite",  # SQLite, shared safely by the web app and the update (a write-ahead log)
    version=_version(),
)


class Settings(core.Settings):
    """The shared package's settings (``raw``, ``data_dir``, ``config_path``, ``section``, ``answer``, ``usage``,
    ``state_db``), with this site's own accessors."""

    # Convenience accessors -------------------------------------------------
    @property
    def archive_start(self) -> date:
        return date.fromisoformat(self.section("archive").get("start_date", "1997-05-01"))

    @property
    def record_dirs(self) -> list[str]:
        return list(self.section("archive").get("record_dirs", []))

    @property
    def ranking(self) -> dict[str, Any]:
        return self.section("ranking")

    @property
    def embedding(self) -> dict[str, Any]:
        return self.section("embedding")

    @property
    def regions(self) -> dict[str, list[str]]:
        return self.section("regions")

    @property
    def sources(self) -> dict[str, Any]:
        return self.section("sources")

    # Data layout -------------------------------------------------------------
    @property
    def raw_xml_dir(self) -> Path:
        return self.data_dir / "raw" / "scrapedxml"

    @property
    def parlparse_dir(self) -> Path:
        return self.data_dir / "raw" / "parlparse"

    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def log_dir(self) -> Path:
        """The structured log of briefings (``briefings.jsonl``: the stages each reached and how it ended; never a
        reader's words) and the weekly reports (``reports/``)."""
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        """The weekly reports, written by ``hansard weekly-report``."""
        return self.log_dir / "reports"


def load_settings(config_path: str | Path | None = None, env: Mapping[str, str] | None = None,
                  **overrides: Any) -> Settings:
    return core.load_settings(Settings, SITE, config_path, env, **overrides)


def save_local_settings(data_dir: Path, updates: Mapping[str, Mapping[str, Any]]) -> Path:
    """Merge simple values into <data_dir>/settings.local.toml, so later commands (sync, serve) use them too."""
    return core.save_local_settings(data_dir, updates, "hansard bootstrap")

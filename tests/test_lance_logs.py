"""LanceDB's own warnings are kept out of the logs (as Where Countries Stand). LanceDB reads LANCEDB_LOG once, when it
is first imported, so each module that imports it sets the level first; checked in a fresh interpreter each time."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

SHOW = "import os, lancedb; print(os.environ.get('LANCEDB_LOG'))"


def _level(first_import: str, **env: str) -> str:
    base = {k: v for k, v in os.environ.items() if k != "LANCEDB_LOG"}
    code = f"import {first_import}; " + SHOW
    out = subprocess.run([sys.executable, "-c", code], env={**base, **env}, capture_output=True, text=True,
                         check=True)
    return out.stdout.strip()


@pytest.mark.parametrize("module", ["hansard.index.lance", "hansard.index.evidence"])
def test_lancedb_shows_only_errors_whichever_module_imports_it_first(module):
    assert _level(module) == "error"


def test_an_explicit_lancedb_log_level_is_kept():
    assert _level("hansard.index.evidence", LANCEDB_LOG="warn") == "warn"

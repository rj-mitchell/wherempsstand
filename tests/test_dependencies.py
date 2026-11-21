"""How the site takes the Where They Stand sites' shared package (wts-core), and that it uses only what the package
makes public."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    [wts_core] = [d for d in project["dependencies"] if canonical(d.split("[")[0].split("@")[0].strip()) == "wts-core"]
    return wts_core


def test_the_shared_package_is_the_wheel_of_a_release_with_the_extras_this_site_uses():
    name, address = (part.strip() for part in requirement().split("@", 1))
    # Typer for the shared commands, httpx for off-site copies, the thread-safe DuckDB connection the vote finder
    # uses, the libraries behind the model, the embedder and the index, and Pillow for the share cards
    assert name == "wts-core[cli,offsite,duckdb,ai,cards]"
    # by its address: the Docker image has no git (wts-core docs/adr/0006)
    assert re.fullmatch(r"https://github\.com/rj-mitchell/wts-core/releases/download/v(\d+\.\d+\.\d+)/"
                        r"wts_core-\1-py3-none-any\.whl", address)
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {canonical(p["name"]): p for p in lock["package"]}
    assert locked["wts-core"]["source"]["url"] == address
    # (the image has git for the site's own need, the members' data it pulls; the package asks for none)


def test_ci_calls_the_shared_workflows_at_the_tag_of_the_package_installed():
    """One number to raise: the release of wts-core in pyproject.toml is the tag every shared workflow and action is
    called at in .github/workflows/ci.yml (wts-core docs/adr/0010)."""
    version = re.search(r"/download/(v[\d.]+)/", requirement())[1]
    called = re.findall(r"uses: rj-mitchell/wts-core/\S+@(\S+)", (ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert len(called) >= 3 and set(called) == {version}
    # and Dependabot leaves both alone, so that it cannot raise one without the other
    assert "rj-mitchell/wts-core*" in (ROOT / ".github" / "dependabot.yml").read_text()


def test_the_site_uses_only_what_the_shared_package_makes_public():
    """Everything the site (and its tests) takes from wts-core is in the ``__all__`` of the module it comes from: the
    package may change anything else without notice (wts-core docs/adr/0007)."""
    import ast
    import importlib

    def public(module: str, name: str) -> bool:
        try:
            importlib.import_module(f"{module}.{name}")  # a module of the package
            return True
        except ImportError:
            return name in importlib.import_module(module).__all__

    checked = 0
    files = [*sorted((ROOT / "src").rglob("*.py")), *sorted((ROOT / "tests").rglob("*.py")),
             *sorted((ROOT / "scripts").rglob("*.py"))]
    for path in files:
        if path.name == "record.py":  # runs only on the code as it was, before the package
            continue
        tree = ast.parse(path.read_text())
        modules: dict[str, str] = {}  # a name in this file -> the module it stands for
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "wts_core":
                for alias in node.names:
                    assert public(node.module, alias.name), f"{path.name}: {node.module}.{alias.name} is private"
                    checked += 1
                    try:
                        importlib.import_module(f"{node.module}.{alias.name}")
                        modules[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                    except ImportError:
                        pass
            elif isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "wts_core" for a in node.names), f"{path.name}: import wts_core"
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules \
                    and not _shadowed(tree, node.value.id):
                assert public(modules[node.value.id], node.attr), f"{path.name}: {modules[node.value.id]}.{node.attr}"
                checked += 1
        # and nothing reached through an object of the package's either: no attribute of its that starts with "_"
        assert not re.search(r"\b(desk|parts|replies|record|core|archive|spending|cache)\._[a-z]", path.read_text()), \
            path.name
    assert checked > 150


def _shadowed(tree, name: str) -> bool:
    """Whether a file also gives the name to something of its own (a variable called ``replies``, say)."""
    import ast

    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store) for n in ast.walk(tree)) \
        or any(isinstance(n, ast.arg) and n.arg == name for n in ast.walk(tree))


def test_no_copy_of_a_replaced_module_is_left():
    """What moved into the package is gone from the site: the files, and the functions that were in them."""
    src = ROOT / "src" / "hansard"
    for gone in ("api/reports.py", "api/statements.py", "backup.py", "offsite.py", "rag/verify.py", "rag/neutral.py",
                 "rag/british.py", "embed/base.py", "embed/__init__.py", "store/shared.py"):
        assert not (src / gone).exists(), gone
    code = "\n".join(p.read_text() for p in src.rglob("*.py"))
    for name in ("class Answerer", "class Embedder", "class FastEmbedEmbedder", "class HashingEmbedder",
                 "class InFlight", "class OffsiteError", "class RateLimiter", "class SharedConnection",
                 "class StatementError", "class Verification", "def add_report", "def add_statement",
                 "def britishise_segments", "def cache_key", "def cached_events", "def cached_system",
                 "def check_caps", "def check_quotes", "def clean_link", "def clean_text", "def close_report",
                 "def decline_statement", "def drop_preambles", "def flag_loaded", "def forget_old_emails",
                 "def list_reports", "def list_statements", "def make_backup", "def make_client",
                 "def model_fingerprint", "def network_of", "def new_briefings_today", "def prices_for",
                 "def publish_statement", "def recheck_flags", "def record_failure", "def record_usage",
                 "def restore_backup", "def store_briefing", "def store_events", "def to_british",
                 "def token_counts", "def update_statement", "def usage_dict", "def verify(",
                 "def withdraw_briefing", "def withdraw_statement", "def sse("):
        assert name not in code, name
    # nor the old tables, in anything the site's own code makes or reads: only the import and the declaration of
    # what becomes of them name them (hansard/store/wts.py)
    for path in src.rglob("*.py"):
        if path.name == "wts.py":
            continue
        assert not re.search(r"\b(FROM|INTO|UPDATE|TABLE IF NOT EXISTS) (usage|answer_cache|briefings|failures|reports|"
                             r"statements)\b", path.read_text()), path.name

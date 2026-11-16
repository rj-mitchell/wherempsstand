"""The outside tools this site is built on, each chosen here from its settings and reached through the Where They
Stand sites' shared package (wts-core, ``wts_core.adapters``): the embedding model that search by meaning uses, and
the AI model that reads questions, checks interests and writes briefings. Everything else in the site takes what
these give, so changing one is a change to this file or to config/settings.toml.
"""

from __future__ import annotations

from collections.abc import Mapping

from wts_core import adapters
from wts_core.ports import Embedder, ModelClient

from hansard.config import SITE, Settings


def get_embedder(settings: Settings) -> Embedder:
    """The embedder ``[embedding]`` names: a small open-source model run on this computer (its weights downloaded
    once into data/models and pinned by their SHA-256; none of the record goes to, or comes from, Hugging Face), or
    the offline hashing embedder that tests and the sample demo use. Each has ``name``, ``dim`` and
    ``similarity_range`` (the cosine of unrelated text and of a strong match), which the ranking reads."""
    cfg = settings.embedding
    backend = cfg.get("backend", "fastembed")
    if backend == "hashing":
        from wts_core.adapters.hashing import HashingEmbedder

        return HashingEmbedder(int(cfg.get("dim", 256)))
    if backend == "fastembed":
        from wts_core.adapters.fastembed import FastEmbedEmbedder

        return FastEmbedEmbedder(cfg["model"], models_dir(settings), threads=cfg.get("threads"),
                                 batch_size=int(cfg.get("batch_size", 64)))
    raise ValueError(f"unknown embedding backend {backend!r}")


def models_dir(settings: Settings):
    """Where the embedding model's weights are kept. The search index records which weights its vectors were made
    with: from wts-core 0.3.0 the embedding model's own files in this folder (before, every model file in it), so
    nothing else is downloaded into it: the re-ranker's model has a folder of its own (hansard/rank/rerank.py).
    A server set up before the shared package may still have the re-ranker's files here too: ``models_folder``
    says when they may be moved."""
    return settings.data_dir / "models"


# What the models folder holds, and so what may be done with it (wts-core docs/UPGRADING.md, "The embedder's id").
# The site's code before the shared package downloaded the re-ranker into data/models beside the embedder, and a
# server set up then still has it there. From wts-core 0.3.0 the embedding model's id covers its own files only,
# where they can be told from the others; the index takes that id when it is first opened on this version (one made
# before the package carries none), and the site's own note of the weights (``embedding_model_sha256``, in the
# state database) takes it at the next `hansard embed`. Until both have, anything moved in the folder changes the
# whole folder's fingerprint, which is what they still carry, and search by meaning would go off until every vector
# is made again.
MODELS_FOLDER = {
    "nothing": "The models folder holds no embedding model yet.",
    "unclear": ("The models folder: the embedding model's files cannot be told from another model's there, so its "
                "id covers every model file in {folder}, as before wts-core 0.3.0. Change nothing in that folder, "
                "the re-ranker's files included: giving each model a folder of its own means making the vectors "
                "again (`hansard embed --start-again`), as one planned step (docs/OPERATIONS.md, \"The models "
                "folder\")."),
    "not yet": ("The models folder: the embedding model's id covers its own files only, but the index or the "
                "site's note of the weights has not yet taken that id on this version: change nothing in {folder} "
                "until it has (the next search by meaning and the next update do it; then run this again)."),
    "ready": ("The models folder: the embedding model's id covers its own files only, and the index and the "
              "site's note of the weights carry it: anything else in {folder} (the re-ranker's files from before "
              "the shared package, say) may be moved or removed."),
}


def models_folder(settings: Settings, fingerprint: str | None = None, unclear: str | None = None,
                  model_id: str | None = None) -> tuple[str, str]:
    """Which of ``MODELS_FOLDER``'s cases the server is in, and its sentence. Reads the index's note and the
    models folder, and the site's note of the weights read-only; loads no model and opens nothing for writing,
    so it can be asked on a new version before the old site is stopped. ``fingerprint``, ``unclear`` and
    ``model_id`` are an embedder's own (``FastEmbedEmbedder``), where one has been made; without them they are
    worked out from the folder as the embedder would."""
    import sqlite3

    from wts_core.adapters.fastembed import model_fingerprints
    from wts_core.adapters.lancedb import stored_meta

    folder, model = models_dir(settings), str(settings.embedding.get("model", ""))
    if fingerprint is None:
        fingerprint, _, unclear = model_fingerprints(folder, model)
        model_id = f"{model}@{fingerprint[:16]}" if fingerprint else model
    if not fingerprint:
        return "nothing", MODELS_FOLDER["nothing"]
    if unclear:
        return "unclear", MODELS_FOLDER["unclear"].format(folder=folder)
    stamped = stored_meta(settings.index_dir)
    noted = None
    if settings.state_db.exists():
        con = sqlite3.connect(f"file:{settings.state_db}?mode=ro", uri=True, timeout=30)
        try:
            row = con.execute("SELECT value FROM kv WHERE key = 'embedding_model_sha256'").fetchone()
            noted = row[0] if row else None
        except sqlite3.Error:
            noted = None
        finally:
            con.close()
    if stamped is not None and stamped.embedder_id == model_id and noted in (None, fingerprint):
        return "ready", MODELS_FOLDER["ready"].format(folder=folder)
    return "not yet", MODELS_FOLDER["not yet"].format(folder=folder)


def has_model(settings: Settings, env: Mapping[str, str] | None = None) -> bool:
    """Whether there is a model to ask (an API key is set): without one the site shows the evidence only."""
    return adapters.has_model(settings, SITE, env)


def model_client(settings: Settings, env: Mapping[str, str] | None = None) -> ModelClient:
    """The model that reads questions and writes briefings. Made once and kept for the life of the process: it
    remembers what the provider refused."""
    return adapters.model_client(settings, SITE, env)

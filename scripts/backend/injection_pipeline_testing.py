"""UAT health check for the ingestion pipeline (loader -> chunker -> embedder -> vector store).

Run from the repo root:
    python -m scripts.backend.injection_pipeline_testing [--stage loader|chunker|embedder|vectorstore|cross|all]
                                                          [--sample-size N] [--report-path FILE]
                                                          [--run-model-checks]

Every path, size and model name this script checks against is read live from
`scripts.backend.backend_settings.settings` - nothing here is hardcoded (see X3).

Read-only: this script never writes to chunked_document.db or the vector store. Three
requirements (L5, C6, X4) ask for a second pipeline run to prove idempotency; re-running the
real loader/chunker/embedder against production output would violate that read-only guarantee
(the chunker truncates its database on every run) and would be expensive, so those three are
answered from static evidence instead - see each one's `details` for what was actually checked.

One check (E4, embedding determinism) needs to load the embedding model to mean anything, which
is comparatively slow, so it's off by default. Pass --run-model-checks to include it.

The corpus this was developed against has a real gap worth knowing up front: the loader never
populates doc_name/author/mode_of_parse, so several checks (C2, C4, X1) can't do true per-document
analysis and will report that honestly (as a warning) rather than fake a result.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import random
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qdrant_client import QdrantClient

from scripts.backend.backend_settings import settings

# Each ingestion module is imported defensively: a stage whose module fails to import (e.g. a
# native dependency like tiktoken blocked by a system policy) shouldn't crash this whole report -
# it should just report that stage's checks as failed with the real reason.
_IMPORT_ERRORS: dict[str, str] = {}

try:
    from scripts.backend.ingestion import chunker as chunker_module
except Exception as e:
    chunker_module = None
    _IMPORT_ERRORS["chunker"] = f"{type(e).__name__}: {e}"

try:
    from scripts.backend.ingestion import embedder as embedder_module
except Exception as e:
    embedder_module = None
    _IMPORT_ERRORS["embedder"] = f"{type(e).__name__}: {e}"

try:
    from scripts.backend.ingestion import loader as loader_module
except Exception as e:
    loader_module = None
    _IMPORT_ERRORS["loader"] = f"{type(e).__name__}: {e}"

try:
    from scripts.backend.ingestion import vector_db as vector_db_module
except Exception as e:
    vector_db_module = None
    _IMPORT_ERRORS["vector_db"] = f"{type(e).__name__}: {e}"

DEFAULT_SAMPLE_SIZE = 50
SIZE_OVERAGE_TOLERANCE = 0.10       # C1: a chunk "exceeds" its budget once tokens > size * (1 + this)
SIZE_VIOLATION_BUDGET = 0.10        # C1: at most this fraction of sampled chunks may exceed
OVERLAP_TOLERANCE = 0.10            # C2: a pair's overlap must land within this fraction of configured
OVERLAP_PASS_RATIO = 0.80           # C2: fraction of pairs that must hit the target - packing works in
                                     # whole semantic units (paragraphs/table-row-groups), so some pairs
                                     # legitimately land outside the token-level tolerance
RECONCILIATION_TOLERANCE = 0.15     # C5
DETERMINISM_MIN_COSINE = 0.999      # E4
QUERY_LATENCY_BUDGET_SECONDS = 1.0  # V5

STAGE_ORDER = ["loader", "chunker", "embedder", "vectorstore", "cross"]
STATUS_LABEL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}

# Display order for the report - natural requirement-ID sequence rather than execution order
# (X3 runs first as a pre-flight gate, X1/X2/X4 run last, but L1..X4 reads better top to bottom).
REQUIREMENT_ORDER = [
    "L1", "L2", "L3", "L4", "L5",
    "C1", "C2", "C3", "C4", "C5", "C6",
    "E1", "E2", "E3", "E4", "E5",
    "V1", "V2", "V3", "V4", "V5",
    "X1", "X2", "X3", "X4",
]

REQUIREMENT_TEXT = {
    "L1": "File discovery coverage",
    "L2": "Unsupported-format handling",
    "L3": "Non-empty extraction",
    "L4": "Output schema validity",
    "L5": "Re-run idempotency",
    "C1": "Parent/child size compliance",
    "C2": "Overlap verification",
    "C3": "Parent-child linkage integrity",
    "C4": "Chunk metadata completeness",
    "C5": "Chunk-to-source reconciliation",
    "C6": "Idempotent re-chunking",
    "E1": "Embedding coverage",
    "E2": "Embedding dimensionality",
    "E3": "Degenerate vector check",
    "E4": "Embedding determinism",
    "E5": "Similarity-metric compatibility",
    "V1": "Vector count reconciliation",
    "V2": "Metadata round-trip",
    "V3": "Duplicate-vector detection",
    "V4": "Similarity-metric configuration",
    "V5": "Query smoke test",
    "X1": "Full-pipeline traceability",
    "X2": "Stage-to-stage reconciliation",
    "X3": "Config-driven checks",
    "X4": "Full-pipeline idempotency",
}


def _sorted_results(results: list[Result]) -> list[Result]:
    return sorted(results, key=lambda res: REQUIREMENT_ORDER.index(res.requirement_id) if res.requirement_id in REQUIREMENT_ORDER else len(REQUIREMENT_ORDER))


@dataclass
class Result:
    requirement_id: str
    stage: str
    status: str  # "pass" | "warn" | "fail" | "skip"
    metric: str
    details: Optional[str] = None


def run_check(requirement_id: str, stage: str, fn) -> Result:
    try:
        status, metric, details = fn()
        return Result(requirement_id, stage, status, metric, details)
    except Exception as e:
        return Result(requirement_id, stage, "fail", "internal error while running this check",
                       f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _scan_source_directory() -> tuple[list[Path], list[Path]]:
    root = Path(settings.loaderSettings.database_file_Path)
    if not root.is_dir():
        return [], []
    exts = set(settings.loaderSettings.acceptable_file_agreement)
    eligible, ineligible = [], []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        (eligible if f.suffix.lstrip(".") in exts else ineligible).append(f)
    return eligible, ineligible


def _load_loaded_document_json() -> Optional[list[dict]]:
    path = Path(settings.loaderSettings.json_save_path)
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _open_chunk_db(require_embeddings: bool = False) -> tuple[Optional[sqlite3.Connection], Optional[str]]:
    db_path = Path(settings.chunkerSettings.chunked_documents_save_path)
    if not db_path.is_file():
        return None, "chunked_document.db not found at the configured path"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if require_embeddings:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "embedding_vector" not in cols or "embedding_vector_name" not in cols:
            conn.close()
            return None, "embedding_vector column not present in chunked_document.db - the embedder has not run yet"
    return conn, None


def _sample_by_type(conn: sqlite3.Connection, chunk_type: str, sample_size: int, require_embedding: bool = False) -> list[sqlite3.Row]:
    clause = "chunk_type = ?" + (" AND embedding_vector IS NOT NULL" if require_embedding else "")
    total = conn.execute(f"SELECT COUNT(*) FROM chunks WHERE {clause}", (chunk_type,)).fetchone()[0]
    if total == 0:
        return []
    if total <= sample_size:
        return conn.execute(f"SELECT * FROM chunks WHERE {clause}", (chunk_type,)).fetchall()
    return conn.execute(f"SELECT * FROM chunks WHERE {clause} ORDER BY RANDOM() LIMIT ?", (chunk_type, sample_size)).fetchall()


def _sample_all(conn: sqlite3.Connection, sample_size: int) -> list[sqlite3.Row]:
    total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if total == 0:
        return []
    if total <= sample_size:
        return conn.execute("SELECT * FROM chunks").fetchall()
    return conn.execute("SELECT * FROM chunks ORDER BY RANDOM() LIMIT ?", (sample_size,)).fetchall()


def _approx_shared_tokens(a: str, b: str) -> int:
    """Longest run of `a`'s tail that matches `b`'s head, in tokens - an approximation of the
    chunker's actual overlap, since the real packer carries forward whole units (paragraphs or
    table-row-groups), not a fixed character count."""
    max_check = min(len(a), len(b))
    for length in range(max_check, 0, -1):
        if a[-length:] == b[:length]:
            return chunker_module.count_tokens(a[-length:]) if chunker_module else length // 4
    return 0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _open_vector_store() -> tuple[Optional[QdrantClient], Optional[str]]:
    try:
        client = QdrantClient(path=str(Path(settings.vectordbSettings.vector_db_save_path)))
    except Exception as e:
        return None, f"could not open vector store: {e} (it may be locked by a running ingestion process)"
    if not client.collection_exists(settings.vectordbSettings.collection_name):
        client.close()
        return None, f"collection '{settings.vectordbSettings.collection_name}' does not exist yet in the vector store"
    return client, None


def config_snapshot() -> dict:
    return {
        "database_file_Path": settings.loaderSettings.database_file_Path,
        "acceptable_file_agreement": settings.loaderSettings.acceptable_file_agreement,
        "json_save_path": settings.loaderSettings.json_save_path,
        "parent_chunk_size": settings.chunkerSettings.parent_chunk_size,
        "parent_chunk_overlap": settings.chunkerSettings.parent_chunk_overlap,
        "child_chunk_size": settings.chunkerSettings.child_chunk_size,
        "child_chunk_overlap": settings.chunkerSettings.child_chunk_overlap,
        "chunked_documents_save_path": settings.chunkerSettings.chunked_documents_save_path,
        "embedding_model_name": settings.embedderSettings.embedding_model_name,
        "embedding_model_save_path": settings.embedderSettings.embedding_model_save_path,
        "vector_db_save_path": settings.vectordbSettings.vector_db_save_path,
        "collection_name": settings.vectordbSettings.collection_name,
    }


# ---------------------------------------------------------------------------
# Loader checks (L1-L5)
# ---------------------------------------------------------------------------

def _check_l1():
    eligible, _ = _scan_source_directory()
    records = _load_loaded_document_json()
    if records is None:
        return "fail", "loaded_document.json not found at the configured json_save_path", \
            f"Expected at {settings.loaderSettings.json_save_path}"

    detail = ("loaded_document.json has no filename/doc identifier field, so this check compares "
               "counts only, not per-file presence. Add a source-path field to the loader's output "
               "to enable true per-file coverage tracking.")

    # loader.py's save_to_json writes to a hardcoded relative path instead of
    # settings.loaderSettings.json_save_path - if that's still true, the file being checked here
    # may not reflect the most recent loader run.
    if loader_module is not None:
        save_src = inspect.getsource(loader_module.save_to_json)
        if settings.loaderSettings.json_save_path not in save_src and "output/loaded_document.json" in save_src:
            detail += (" NOTE: loader.save_to_json currently writes to a hardcoded path "
                        "('output/loaded_document.json', relative to cwd) that does not match "
                        f"settings.loaderSettings.json_save_path ('{settings.loaderSettings.json_save_path}') "
                        "- the file checked here may be stale relative to the last loader run.")
    else:
        detail += f" (could not statically check for a loader output-path mismatch: {_IMPORT_ERRORS.get('loader')})"

    eligible_count, loaded_count = len(eligible), len(records)
    if loaded_count == eligible_count:
        return "pass", f"{loaded_count}/{eligible_count} eligible files represented (count match)", detail
    if loaded_count < eligible_count:
        return "fail", f"only {loaded_count}/{eligible_count} eligible files represented - possible silent skip(s)", detail
    return "warn", f"{loaded_count} records vs {eligible_count} eligible files - more records than source files", detail


def _check_l2():
    _, ineligible = _scan_source_directory()
    if loader_module is None:
        return "fail", f"could not import loader module: {_IMPORT_ERRORS.get('loader')}", None
    src = inspect.getsource(loader_module.load_all_local_files)
    filters_before_parse = "acceptable_file_agreement" in src and "paths_of_acceptable_files" in src
    if not filters_before_parse:
        return "warn", "could not statically confirm the loader filters unsupported extensions before parsing", None
    if not ineligible:
        return "pass", "no unsupported-format files found in corpus; loader filters by extension before parsing (verified in source)", None
    return "warn", f"{len(ineligible)} unsupported-format file(s) found in corpus; excluded before parsing but not individually logged", \
        "Pass criterion asks for 'every skip logged with reason' - unsupported-extension skips are currently silent (only FileSafetyError skips, e.g. oversized/zip-bomb files, are logged)."


def _check_l3():
    records = _load_loaded_document_json()
    if records is None:
        return "fail", "loaded_document.json not found", None
    if not records:
        return "warn", "loaded_document.json is empty - nothing to check", None
    non_empty = sum(1 for rec in records if isinstance(rec, dict) and rec.get("text_content"))
    ratio = non_empty / len(records)
    return ("pass" if ratio >= 0.98 else "fail"), f"{non_empty}/{len(records)} records ({ratio:.1%}) have non-empty text_content", None


def _check_l4():
    path = Path(settings.loaderSettings.json_save_path)
    if not path.is_file():
        return "fail", "loaded_document.json not found", None
    try:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except json.JSONDecodeError as e:
        return "fail", "loaded_document.json is not valid JSON", str(e)
    if not isinstance(records, list):
        return "fail", "loaded_document.json root is not a list of records", None

    hard_required = ["text_content"]
    soft_required = ["doc_name", "author", "mode_of_parse"]
    missing_hard = sum(1 for rec in records if not isinstance(rec, dict) or any(k not in rec for k in hard_required))
    if missing_hard:
        return "fail", f"{missing_hard}/{len(records)} record(s) missing required key(s) {hard_required}", None

    fully_missing = [k for k in soft_required if records and all(not rec.get(k) for rec in records if isinstance(rec, dict))]
    if fully_missing:
        return "warn", f"valid JSON, {len(records)} well-formed record(s)", \
            f"loader never populates {fully_missing} - every record has these null/missing, so downstream chunk metadata will also be empty (see C4)."
    return "pass", f"valid JSON, {len(records)} well-formed record(s)", None


def _check_l5():
    if loader_module is None:
        return "fail", f"could not import loader module: {_IMPORT_ERRORS.get('loader')}", None
    src = inspect.getsource(loader_module.secure_json_dump)
    truncates = "O_TRUNC" in src
    detail = ("Structural check, not a literal two-run execution: re-running the loader against the "
               "real source folder is expensive and would violate the read-only requirement if its "
               "output path happened to be the configured one. Verified instead that the writer "
               "truncates and fully rewrites the output file on every write (O_TRUNC) rather than "
               "appending, so a re-run on an unchanged folder is idempotent by construction.")
    if truncates:
        return "pass", "loader output write path uses truncate-and-rewrite (verified in source)", detail
    return "warn", "could not verify truncate-on-write behavior in loader source", detail


def loader_checks(_args) -> list[Result]:
    stage = "loader"
    return [
        run_check("L1", stage, _check_l1),
        run_check("L2", stage, _check_l2),
        run_check("L3", stage, _check_l3),
        run_check("L4", stage, _check_l4),
        run_check("L5", stage, _check_l5),
    ]


# ---------------------------------------------------------------------------
# Chunker checks (C1-C6)
# ---------------------------------------------------------------------------

def _check_c1(sample_size):
    conn, err = _open_chunk_db()
    if conn is None:
        return "fail", err, None
    try:
        sizes = {"parent": settings.chunkerSettings.parent_chunk_size, "child": settings.chunkerSettings.child_chunk_size}
        violations, total_sampled = {}, 0
        for chunk_type, size in sizes.items():
            rows = _sample_by_type(conn, chunk_type, sample_size)
            total_sampled += len(rows)
            limit = size * (1 + SIZE_OVERAGE_TOLERANCE)
            over = sum(1 for row in rows if (row["tokens"] or 0) > limit)
            violations[chunk_type] = (over, len(rows))
        if total_sampled == 0:
            return "warn", "no chunks found to sample", None
        total_over = sum(v[0] for v in violations.values())
        ratio = total_over / total_sampled
        status = "pass" if ratio <= SIZE_VIOLATION_BUDGET else "fail"
        detail = ", ".join(f"{t}: {o}/{n} over size" for t, (o, n) in violations.items())
        return status, f"{total_over}/{total_sampled} sampled chunks ({ratio:.1%}) exceed configured size by >{SIZE_OVERAGE_TOLERANCE:.0%}", detail
    finally:
        conn.close()


def _check_c2(sample_size):
    conn, err = _open_chunk_db()
    if conn is None:
        return "fail", err, None
    try:
        doc_names = [row[0] for row in conn.execute("SELECT DISTINCT doc_name FROM chunks WHERE doc_name IS NOT NULL").fetchall()]
        if not doc_names:
            return "warn", "no non-null doc_name values in the corpus - cannot group chunks by document to measure overlap", \
                "The loader does not currently populate doc_name (see L4), so there's no reliable way to identify which chunks are consecutive within the same source document."

        sample_docs = random.sample(doc_names, min(sample_size, len(doc_names)))
        configured = {"parent": settings.chunkerSettings.parent_chunk_overlap, "child": settings.chunkerSettings.child_chunk_overlap}
        within_tol, total_pairs = 0, 0
        for doc in sample_docs:
            for chunk_type, overlap_cfg in configured.items():
                rows = conn.execute(
                    "SELECT text_content FROM chunks WHERE doc_name = ? AND chunk_type = ? ORDER BY rowid",
                    (doc, chunk_type),
                ).fetchall()
                for a, b in zip(rows, rows[1:]):
                    overlap_tokens = _approx_shared_tokens(a["text_content"], b["text_content"])
                    total_pairs += 1
                    if abs(overlap_tokens - overlap_cfg) <= overlap_cfg * OVERLAP_TOLERANCE + 1:
                        within_tol += 1
        if total_pairs == 0:
            return "warn", "no consecutive same-document chunk pairs found to measure overlap", None
        ratio = within_tol / total_pairs
        status = "pass" if ratio >= OVERLAP_PASS_RATIO else "fail"
        return status, f"{within_tol}/{total_pairs} consecutive chunk pairs ({ratio:.1%}) have overlap within {OVERLAP_TOLERANCE:.0%} of configured value", \
            "Overlap is approximated from the longest matching run across the chunk boundary, since the chunker's unit-packing (whole paragraphs/table-row-groups) produces variable actual overlap within its token budget."
    finally:
        conn.close()


def _check_c3():
    conn, err = _open_chunk_db()
    if conn is None:
        return "fail", err, None
    try:
        orphans = conn.execute(
            "SELECT COUNT(*) FROM chunks c WHERE c.chunk_type='child' AND c.parent_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM chunks p WHERE p.uuid = c.parent_id AND p.chunk_type='parent')"
        ).fetchone()[0]
        null_parent_children = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='child' AND parent_id IS NULL").fetchone()[0]
        childless_parents = conn.execute(
            "SELECT COUNT(*) FROM chunks p WHERE p.chunk_type='parent' "
            "AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.chunk_type='child' AND c.parent_id = p.uuid)"
        ).fetchone()[0]
        total_parents = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='parent'").fetchone()[0]
        orphan_total = orphans + null_parent_children
        status = "pass" if orphan_total == 0 and childless_parents == 0 else "fail"
        return status, f"orphaned children: {orphan_total}, childless parents: {childless_parents}/{total_parents}", \
            "parent_id carries a foreign-key constraint enforced at insert time, so orphans should be structurally impossible; this re-verifies it directly."
    finally:
        conn.close()


def _check_c4(sample_size):
    conn, err = _open_chunk_db()
    if conn is None:
        return "fail", err, None
    try:
        rows = _sample_all(conn, sample_size)
        if not rows:
            return "warn", "no chunks found", None
        n = len(rows)
        uuids = [row["uuid"] for row in rows]
        hard_fields = {
            "uuid": sum(1 for row in rows if not row["uuid"]),
            "chunk_type": sum(1 for row in rows if row["chunk_type"] not in ("parent", "child")),
            "tokens": sum(1 for row in rows if not row["tokens"] or row["tokens"] <= 0),
        }
        soft_fields = {
            "doc_name": sum(1 for row in rows if not row["doc_name"]),
            "mode_of_parse": sum(1 for row in rows if not row["mode_of_parse"]),
        }
        duplicate_uuids = n - len(set(uuids))
        hard_problems = sum(hard_fields.values()) + duplicate_uuids
        soft_problems = sum(soft_fields.values())
        status = "fail" if hard_problems else ("warn" if soft_problems else "pass")
        detail = f"hard-field issues: {hard_fields}, duplicate uuids: {duplicate_uuids}, soft-field (doc_name/mode_of_parse) missing: {soft_fields}"
        return status, f"{n} chunk(s) sampled, {hard_problems} hard-field issue(s), {soft_problems} soft-field issue(s)", detail
    finally:
        conn.close()


def _check_c5():
    records = _load_loaded_document_json()
    conn, err = _open_chunk_db()
    if records is None or conn is None:
        return "fail", err or "loaded_document.json not found", None
    try:
        total_doc_tokens = sum((rec.get("token count", 0) or 0) for rec in records if isinstance(rec, dict))
        if total_doc_tokens == 0:
            return "warn", "could not compute total source token count (missing or zero 'token count' fields)", None

        parent_step = max(1, settings.chunkerSettings.parent_chunk_size - settings.chunkerSettings.parent_chunk_overlap)
        expected_parent = math.ceil(total_doc_tokens / parent_step)
        actual_parent = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='parent'").fetchone()[0]
        total_parent_tokens = conn.execute("SELECT COALESCE(SUM(tokens),0) FROM chunks WHERE chunk_type='parent'").fetchone()[0]

        child_step = max(1, settings.chunkerSettings.child_chunk_size - settings.chunkerSettings.child_chunk_overlap)
        expected_child = math.ceil(total_parent_tokens / child_step) if total_parent_tokens else 0
        actual_child = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='child'").fetchone()[0]

        def deviation(expected, actual):
            return 0.0 if expected == 0 else abs(actual - expected) / expected

        dev_parent, dev_child = deviation(expected_parent, actual_parent), deviation(expected_child, actual_child)
        status = "pass" if dev_parent <= RECONCILIATION_TOLERANCE and dev_child <= RECONCILIATION_TOLERANCE else "fail"
        return status, (f"parent: {actual_parent} actual vs ~{expected_parent} expected ({dev_parent:.1%} dev); "
                         f"child: {actual_child} actual vs ~{expected_child} expected ({dev_child:.1%} dev)"), \
            ("Expected counts are a rough token-budget estimate (total tokens / (chunk_size - overlap)) and "
             "don't account for forced section boundaries: the chunker starts a new chunk at every markdown "
             "header, repeated letterhead/banner line, and table-row-group regardless of the token budget, so "
             "a document with many such boundaries (e.g. tables, multi-section reports) will produce more, "
             "smaller chunks than this formula predicts. A large deviation here is a cue to recalibrate the "
             "tolerance or formula for this corpus's document shape, not necessarily a pipeline defect - "
             "cross-check against C1 (size compliance) before treating it as one.")
    finally:
        conn.close()


def _check_c6():
    if chunker_module is None:
        return "fail", f"could not import chunker module: {_IMPORT_ERRORS.get('chunker')}", None
    src = inspect.getsource(chunker_module._get_db_connection)
    wipes_on_fresh_run = "unlink" in src
    pk_enforced = "PRIMARY KEY" in src
    detail = ("Structural check, not a literal two-run execution: re-running the chunker against the "
               "production database would violate the read-only requirement and would trigger a full "
               "re-embed/re-store downstream. Verified instead that each fresh process run deletes the "
               "existing chunked_document.db before writing, and that `uuid` is the table's PRIMARY KEY, "
               "so duplicate rows cannot accumulate within or across runs.")
    if wipes_on_fresh_run and pk_enforced:
        return "pass", "chunker truncates chunked_document.db on each run and enforces a uuid PRIMARY KEY (verified in source)", detail
    return "warn", "could not verify overwrite-and-primary-key behavior in chunker source", detail


def chunker_checks(args) -> list[Result]:
    stage = "chunker"
    return [
        run_check("C1", stage, lambda: _check_c1(args.sample_size)),
        run_check("C2", stage, lambda: _check_c2(args.sample_size)),
        run_check("C3", stage, _check_c3),
        run_check("C4", stage, lambda: _check_c4(args.sample_size)),
        run_check("C5", stage, _check_c5),
        run_check("C6", stage, _check_c6),
    ]


# ---------------------------------------------------------------------------
# Embedder checks (E1-E5)
# ---------------------------------------------------------------------------

def _check_e1():
    conn, err = _open_chunk_db()
    if conn is None:
        return "fail", err, None
    try:
        total_child = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='child'").fetchone()[0]
        if total_child == 0:
            return "warn", "no child chunks found", None
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "embedding_vector" not in cols:
            return "fail", "embedding_vector column not present - the embedder has not run yet", None
        missing = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE chunk_type='child' AND "
            "(embedding_vector IS NULL OR embedding_vector_name IS NULL OR embedding_vector_name != ?)",
            (settings.embedderSettings.embedding_model_name,),
        ).fetchone()[0]
        status = "pass" if missing == 0 else "fail"
        return status, f"{total_child - missing}/{total_child} child chunks have an embedding tagged '{settings.embedderSettings.embedding_model_name}'", \
            "Only child chunks are embedded by design (parents are retained for context only); this check is scoped accordingly."
    finally:
        conn.close()


def _check_e2(sample_size):
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        rows = _sample_by_type(conn, "child", sample_size, require_embedding=True)
        if not rows:
            return "warn", "no embedded chunks to sample", None
        lengths = [len(json.loads(row["embedding_vector"])) for row in rows]
        distinct = set(lengths)
        if len(distinct) == 1:
            return "pass", f"{len(rows)} sampled vector(s), consistent dimension = {distinct.pop()}", None
        return "fail", f"inconsistent vector dimensions across sample: {dict(Counter(lengths))}", None
    finally:
        conn.close()


def _check_e3(sample_size):
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        rows = _sample_by_type(conn, "child", sample_size, require_embedding=True)
        if not rows:
            return "warn", "no embedded chunks to sample", None
        offenders = []
        for row in rows:
            vec = json.loads(row["embedding_vector"])
            if all(v == 0 for v in vec):
                offenders.append((row["uuid"], "all-zero"))
            elif any(math.isnan(v) for v in vec):
                offenders.append((row["uuid"], "NaN"))
            elif any(math.isinf(v) for v in vec):
                offenders.append((row["uuid"], "Inf"))
        status = "pass" if not offenders else "fail"
        detail = "; ".join(f"{u} ({reason})" for u, reason in offenders[:10]) or None
        return status, f"{len(offenders)}/{len(rows)} sampled vector(s) degenerate", detail
    finally:
        conn.close()


def _check_e4(enabled):
    if not enabled:
        return "skip", "skipped by default (loads the embedding model) - pass --run-model-checks to enable", None
    if embedder_module is None:
        return "fail", f"could not import embedder module: {_IMPORT_ERRORS.get('embedder')}", None
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        row = conn.execute(
            "SELECT text_content FROM chunks WHERE chunk_type='child' AND embedding_vector IS NOT NULL ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        if row is None:
            return "warn", "no embedded chunk available to test determinism", None
        from sentence_transformers import SentenceTransformer
        model_path = embedder_module.initiate_embedding_logic()
        model = SentenceTransformer(str(model_path))
        v1 = embedder_module.embed_document(row["text_content"], model)
        v2 = embedder_module.embed_document(row["text_content"], model)
        if v1 is None or v2 is None:
            return "fail", "embedding call failed", None
        sim = cosine_similarity(v1, v2)
        return ("pass" if sim >= DETERMINISM_MIN_COSINE else "fail"), f"cosine similarity between two embeddings of the same text: {sim:.6f}", None
    finally:
        conn.close()


def _vector_store_distance() -> Optional[str]:
    client = _open_vector_store()[0]
    if client is None:
        return None
    try:
        info = client.get_collection(collection_name=settings.vectordbSettings.collection_name)
        return str(info.config.params.vectors.distance.value)
    except Exception:
        return None
    finally:
        client.close()


def _check_e5(sample_size):
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        rows = _sample_by_type(conn, "child", sample_size, require_embedding=True)
        if not rows:
            return "warn", "no embedded chunks to sample", None
        norms = [math.sqrt(sum(v * v for v in json.loads(row["embedding_vector"]))) for row in rows]
        avg_norm = sum(norms) / len(norms)
        normalized = all(abs(n - 1.0) < 0.01 for n in norms)
        store_distance = _vector_store_distance()
        if store_distance is None:
            return "warn", f"average embedding L2 norm = {avg_norm:.3f} (normalized={normalized}); could not read the vector store's configured metric to cross-check", None
        if store_distance == "Dot" and normalized:
            return "warn", f"store metric is DOT but embeddings are unit-normalized (avg norm {avg_norm:.3f}) - mathematically equivalent to cosine here, so not broken, just redundant; confirm this is intentional", None
        if store_distance == "Cosine" and not normalized:
            return "warn", f"store metric is COSINE but embeddings aren't unit-normalized (avg norm {avg_norm:.3f}) - Qdrant normalizes internally for cosine scoring so results should still be correct", None
        return "pass", f"vector store metric={store_distance}, average embedding L2 norm={avg_norm:.3f} (normalized={normalized}) - consistent", None
    finally:
        conn.close()


def embedder_checks(args) -> list[Result]:
    stage = "embedder"
    return [
        run_check("E1", stage, _check_e1),
        run_check("E2", stage, lambda: _check_e2(args.sample_size)),
        run_check("E3", stage, lambda: _check_e3(args.sample_size)),
        run_check("E4", stage, lambda: _check_e4(args.run_model_checks)),
        run_check("E5", stage, lambda: _check_e5(args.sample_size)),
    ]


# ---------------------------------------------------------------------------
# Vector store checks (V1-V5)
# ---------------------------------------------------------------------------

def _check_v1(client):
    collection = settings.vectordbSettings.collection_name
    vector_count = client.count(collection_name=collection, exact=True).count
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        db_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='child' AND embedding_vector IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    drift = vector_count - db_count
    detail = None
    if drift > 0:
        detail = ("The vector store has more points than the current database's embedded chunks. Since "
                   "the chunker assigns a fresh random uuid on every run and the vector store is only "
                   "ever upserted into (never cleared), points from previous ingestion runs accumulate "
                   "indefinitely instead of being replaced - see X4.")
    elif drift < 0:
        detail = "The vector store has fewer points than embedded chunks in the database - some embedded chunks were never stored."
    return ("pass" if drift == 0 else "fail"), f"vector store: {vector_count} points, chunked_document.db: {db_count} embedded child chunks (drift: {drift:+d})", detail


def _check_v2(client, sample_size):
    collection = settings.vectordbSettings.collection_name
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        rows = _sample_by_type(conn, "child", sample_size, require_embedding=True)
    finally:
        conn.close()
    if not rows:
        return "warn", "no embedded chunks to sample", None

    ids = [row["uuid"] for row in rows]
    points = {p.id: p.payload for p in client.retrieve(collection_name=collection, ids=ids, with_payload=True)}
    checked_fields = ["doc_name", "author", "mode_of_parse", "tokens"]
    mismatches = missing_points = 0
    for row in rows:
        payload = points.get(row["uuid"])
        if payload is None:
            missing_points += 1
            continue
        if any(payload.get(f) != row[f] for f in checked_fields):
            mismatches += 1
    status = "pass" if mismatches == 0 and missing_points == 0 else "fail"
    return status, (f"{len(rows) - mismatches - missing_points}/{len(rows)} sampled chunks round-trip correctly "
                     f"({missing_points} not found in store, {mismatches} field mismatch(es))"), \
        "chunk_type is not stored as a payload field in the vector store (only text_content/doc_name/author/mode_of_parse/tokens/parent_id are), and uuid is only the point ID, not a payload field - this check verifies id round-trip via retrieve-by-id plus the other four fields."


def _check_v3(client):
    collection = settings.vectordbSettings.collection_name
    ids, offset = [], None
    while True:
        points, offset = client.scroll(collection_name=collection, limit=1000, offset=offset, with_payload=False, with_vectors=False)
        ids.extend(p.id for p in points)
        if offset is None:
            break
    duplicates = len(ids) - len(set(ids))
    return ("pass" if duplicates == 0 else "fail"), f"{len(ids)} point(s) scanned, {duplicates} duplicate id(s)", \
        "Qdrant enforces point-ID uniqueness on upsert, so exact duplicate ids shouldn't be reachable; this re-verifies that invariant directly."


def _check_v4(client):
    collection = settings.vectordbSettings.collection_name
    info = client.get_collection(collection_name=collection)
    actual = info.config.params.vectors.distance.value
    model_name = settings.embedderSettings.embedding_model_name
    expected = "Dot" if "dot" in model_name.lower() else "Cosine"
    return ("pass" if actual == expected else "fail"), f"configured distance metric = {actual}, expected {expected} for model '{model_name}'", None


def _check_v5(client):
    collection = settings.vectordbSettings.collection_name
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        row = conn.execute(
            "SELECT embedding_vector FROM chunks WHERE chunk_type='child' AND embedding_vector IS NOT NULL ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return "warn", "no embedded chunk available to use as a query vector", None

    query_vector = json.loads(row["embedding_vector"])
    k = 5
    start = time.time()
    try:
        response = client.query_points(collection_name=collection, query=query_vector, limit=k, with_payload=False)
    except Exception as e:
        return "fail", f"query raised an error: {e}", None
    elapsed = time.time() - start
    n_results = len(response.points)
    status = "pass" if n_results > 0 and elapsed < QUERY_LATENCY_BUDGET_SECONDS else "fail"
    return status, f"query returned {n_results} result(s) in {elapsed * 1000:.1f}ms (budget {QUERY_LATENCY_BUDGET_SECONDS * 1000:.0f}ms)", None


def vectorstore_checks(args) -> list[Result]:
    stage = "vectorstore"
    client, err = _open_vector_store()
    if client is None:
        return [Result(rid, stage, "fail", err, None) for rid in ("V1", "V2", "V3", "V4", "V5")]
    try:
        return [
            run_check("V1", stage, lambda: _check_v1(client)),
            run_check("V2", stage, lambda: _check_v2(client, args.sample_size)),
            run_check("V3", stage, lambda: _check_v3(client)),
            run_check("V4", stage, lambda: _check_v4(client)),
            run_check("V5", stage, lambda: _check_v5(client)),
        ]
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Cross-stage checks (X1-X4)
# ---------------------------------------------------------------------------

REQUIRED_SETTINGS = [
    ("loaderSettings.database_file_Path", lambda s: s.loaderSettings.database_file_Path),
    ("loaderSettings.acceptable_file_agreement", lambda s: s.loaderSettings.acceptable_file_agreement),
    ("loaderSettings.json_save_path", lambda s: s.loaderSettings.json_save_path),
    ("chunkerSettings.parent_chunk_size", lambda s: s.chunkerSettings.parent_chunk_size),
    ("chunkerSettings.parent_chunk_overlap", lambda s: s.chunkerSettings.parent_chunk_overlap),
    ("chunkerSettings.child_chunk_size", lambda s: s.chunkerSettings.child_chunk_size),
    ("chunkerSettings.child_chunk_overlap", lambda s: s.chunkerSettings.child_chunk_overlap),
    ("chunkerSettings.chunked_documents_save_path", lambda s: s.chunkerSettings.chunked_documents_save_path),
    ("embedderSettings.embedding_model_name", lambda s: s.embedderSettings.embedding_model_name),
    ("embedderSettings.embedding_model_save_path", lambda s: s.embedderSettings.embedding_model_save_path),
    ("vectordbSettings.vector_db_save_path", lambda s: s.vectordbSettings.vector_db_save_path),
    ("vectordbSettings.collection_name", lambda s: s.vectordbSettings.collection_name),
]


def _check_x3():
    missing = []
    for name, getter in REQUIRED_SETTINGS:
        try:
            if getter(settings) is None:
                missing.append(name)
        except AttributeError:
            missing.append(name)
    if missing:
        return "fail", f"settings object is missing or has renamed field(s): {missing}", \
            "Every check in this script reads these fields from `settings` at runtime rather than hardcoding them, so this failing means the rest of the suite can't run meaningfully."
    return "pass", f"all {len(REQUIRED_SETTINGS)} required settings fields are present and read live from `settings`", None


def _check_x1(sample_size):
    conn, err = _open_chunk_db(require_embeddings=True)
    if conn is None:
        return "fail", err, None
    try:
        doc_names = [row[0] for row in conn.execute("SELECT DISTINCT doc_name FROM chunks WHERE doc_name IS NOT NULL").fetchall()]
        if not doc_names:
            return "warn", "no non-null doc_name values in the corpus - cannot trace individual source files through the pipeline", \
                "The loader does not currently populate doc_name/author/mode_of_parse (see L4), so chunks can't be tied back to a specific source file by name yet."

        sample = random.sample(doc_names, min(sample_size, len(doc_names)))
        client, _ = _open_vector_store()
        broken = []
        try:
            for doc in sample:
                parents = conn.execute("SELECT uuid FROM chunks WHERE doc_name=? AND chunk_type='parent'", (doc,)).fetchall()
                if not parents:
                    broken.append((doc, "no parent chunks"))
                    continue
                parent_ids = [p["uuid"] for p in parents]
                placeholders = ",".join("?" * len(parent_ids))
                children = conn.execute(
                    f"SELECT uuid, embedding_vector FROM chunks WHERE chunk_type='child' AND parent_id IN ({placeholders})",
                    parent_ids,
                ).fetchall()
                if not children:
                    broken.append((doc, "no child chunks linked to its parents"))
                    continue
                embedded = [c for c in children if c["embedding_vector"] is not None]
                if not embedded:
                    broken.append((doc, "no embedded child chunks"))
                    continue
                if client is not None and not client.retrieve(collection_name=settings.vectordbSettings.collection_name, ids=[embedded[0]["uuid"]]):
                    broken.append((doc, "embedded chunk missing from vector store"))
        finally:
            if client is not None:
                client.close()

        status = "pass" if not broken else "fail"
        detail = "; ".join(f"{d}: {reason}" for d, reason in broken[:10]) or None
        return status, f"{len(sample) - len(broken)}/{len(sample)} sampled source file(s) traced end to end", detail
    finally:
        conn.close()


def _check_x2():
    eligible, _ = _scan_source_directory()
    records = _load_loaded_document_json()
    loaded = len(records) if records else 0

    conn, err = _open_chunk_db()
    if conn is None:
        return "fail", err, None
    try:
        parent = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='parent'").fetchone()[0]
        child = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='child'").fetchone()[0]
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        embedded = conn.execute("SELECT COUNT(*) FROM chunks WHERE chunk_type='child' AND embedding_vector IS NOT NULL").fetchone()[0] if "embedding_vector" in cols else 0
    finally:
        conn.close()

    stored = None
    client, _ = _open_vector_store()
    if client is not None:
        try:
            stored = client.count(collection_name=settings.vectordbSettings.collection_name, exact=True).count
        finally:
            client.close()

    table = (f"discovered={len(eligible)} -> loaded={loaded} -> parent_chunks={parent} -> "
             f"child_chunks={child} -> embedded={embedded} -> stored_vectors={stored if stored is not None else 'unavailable'}")

    hard_drops, soft_drops = [], []
    if loaded < len(eligible):
        hard_drops.append("loaded < discovered")
    if embedded < child:
        hard_drops.append("embedded < child_chunks")
    if stored is not None and stored != embedded:
        soft_drops.append(f"stored ({stored}) != embedded ({embedded}) - see V1 for detail")

    status = "fail" if hard_drops else ("warn" if soft_drops else "pass")
    details = "; ".join(hard_drops + soft_drops) or None
    return status, table, details


def _check_x4():
    if chunker_module is None or vector_db_module is None:
        missing = [name for name, mod in (("chunker", chunker_module), ("vector_db", vector_db_module)) if mod is None]
        return "fail", f"could not import module(s) needed for this check: {missing}", \
            "; ".join(f"{m}: {_IMPORT_ERRORS.get(m)}" for m in missing)
    regenerates_uuid = "uuid.uuid4" in inspect.getsource(chunker_module.create_parent_chunk)
    vdb_src = inspect.getsource(vector_db_module.add_chunks_to_vector)
    reset_fn = getattr(vector_db_module, "_reset_collection_once", None)
    resets_before_upsert = "delete_collection" in (vdb_src + inspect.getsource(reset_fn) if reset_fn else vdb_src)
    detail = ("Structural check, not a literal two-run execution: running the full pipeline twice "
               "against production data - including re-embedding with the model - would violate the "
               "read-only requirement and is expensive. The chunker assigns a brand-new random uuid to "
               "every chunk on every run, so the vector store either needs to be wiped and rebuilt each "
               "run, or it accumulates stale points from every previous run's uuids forever. See V1/X2 "
               "for direct evidence of the current state from the data.")
    if resets_before_upsert:
        return "pass", "vector store collection is wiped and rebuilt once per run before upserting (verified in source), so re-running end to end converges to the current chunk set instead of growing", detail
    if regenerates_uuid:
        return "fail", "pipeline is not idempotent by construction: fresh uuids per run + upsert-only vector store means re-running end to end grows the vector store instead of converging", detail
    return "warn", "could not statically confirm idempotency behavior from source", detail


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def overall_verdict(results: list[Result]) -> str:
    if any(res.status == "fail" for res in results):
        return "FAIL"
    if any(res.status in ("warn", "skip") for res in results):
        return "PASS WITH WARNINGS"
    return "PASS"


def print_report(results: list[Result], args, started_at: datetime) -> None:
    print("=" * 78)
    print("INGESTION PIPELINE UAT HEALTH REPORT")
    print(f"timestamp: {started_at.isoformat()}")
    print(f"stage(s) run: {args.stage}   sample size: {args.sample_size}   model checks: {'on' if args.run_model_checks else 'off'}")
    print("-" * 78)
    print("config under test (read live from settings):")
    for key, value in config_snapshot().items():
        print(f"  {key}: {value}")
    print("=" * 78)
    print()

    for res in _sorted_results(results):
        label = STATUS_LABEL.get(res.status, res.status.upper())
        reason = res.metric
        if res.details:
            reason += f" | {res.details}"
        print(f'REQUIREMENT - {res.requirement_id} - "{REQUIREMENT_TEXT.get(res.requirement_id, "")}"')
        print(f"ANALYSIS - {label}")
        print(f"REASON - {reason}")
        print()
        print("-" * 70)
        print()

    verdict = overall_verdict(results)
    counts = {status: sum(1 for res in results if res.status == status) for status in ("pass", "warn", "fail", "skip")}
    print("=" * 78)
    print(f"OVERALL VERDICT: {verdict}   (pass={counts['pass']} warn={counts['warn']} fail={counts['fail']} skip={counts['skip']})")
    print("=" * 78)


def write_json_report(results: list[Result], args, started_at: datetime, verdict: str) -> None:
    payload = {
        "timestamp": started_at.isoformat(),
        "config": config_snapshot(),
        "overall_verdict": verdict,
        "requirements": [asdict(res) for res in _sorted_results(results)],
    }
    path = Path(args.report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nreport written to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="UAT health check for the ingestion pipeline (loader, chunker, embedder, vector store).")
    parser.add_argument("--stage", choices=["loader", "chunker", "embedder", "vectorstore", "cross", "all"], default="all",
                         help="Run only this stage's checks (default: all stages + cross-stage).")
    parser.add_argument("--full", action="store_true", help="Equivalent to --stage all.")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE,
                         help="Max rows to sample for checks that don't scan the full corpus (default: %(default)s).")
    parser.add_argument("--report-path", type=str, default=None, help="If given, also write the JSON health report to this path.")
    parser.add_argument("--run-model-checks", action="store_true",
                         help="Enable checks that load the embedding model (currently just E4, embedding determinism). Off by default since it's comparatively slow.")
    args = parser.parse_args()
    if args.full:
        args.stage = "all"
    return args


def main():
    args = parse_args()
    started_at = datetime.now(timezone.utc)
    results: list[Result] = []

    x3 = run_check("X3", "cross", _check_x3)
    results.append(x3)
    if x3.status == "fail":
        print_report(results, args, started_at)
        sys.exit(1)

    stage = args.stage
    if stage in ("loader", "all"):
        results += loader_checks(args)
    if stage in ("chunker", "all"):
        results += chunker_checks(args)
    if stage in ("embedder", "all"):
        results += embedder_checks(args)
    if stage in ("vectorstore", "all"):
        results += vectorstore_checks(args)
    if stage in ("cross", "all"):
        results.append(run_check("X1", "cross", lambda: _check_x1(args.sample_size)))
        results.append(run_check("X2", "cross", _check_x2))
        results.append(run_check("X4", "cross", _check_x4))

    print_report(results, args, started_at)
    verdict = overall_verdict(results)
    if args.report_path:
        write_json_report(results, args, started_at, verdict)

    sys.exit(1 if verdict == "FAIL" else 0)


if __name__ == "__main__":
    main()

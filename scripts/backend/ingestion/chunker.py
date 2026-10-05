indentation = '--- --- '*2
import time
start_time = time.time()
def _lap() -> str:
    return f"{time.time() - start_time:.2f}s"


from typing import List
from pathlib import Path
import json
import os
import re
import uuid
import sqlite3
import tiktoken


print(f"{indentation}IMPORTED chunker")

from scripts.backend.backend_settings import settings, chunkObjectClass


def access_document() -> Path:
    try:
        json_path = Path(settings.chunkerSettings.json_saved_path)
        print(f"[{_lap()}]{indentation} {json_path}")

        if json_path.is_file():
            return json_path

        return None

    except Exception as e:
        print(f"[{_lap()}]{indentation} ERROR: couldnt access json file: Error => {e}")
        return None



def get_json_batch(json_path: Path, offset: int, batch_size: int) -> tuple[list[dict], int]:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    batch = data[offset : offset + batch_size]
    return batch, offset + len(batch)

def get_summary_of_the_chunk():
    return 'sumarr==y'


encoding = tiktoken.get_encoding("cl100k_base")
def count_tokens(text: str) -> int:
    return len(encoding.encode(text))


_HEADER_PATTERN = re.compile(r"(?m)^(#{1,3})\s+(.*)$")
_TABLE_SEPARATOR_ROW_PATTERN = re.compile(r"^[\s|:\-]+$")


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def _is_table_separator_row(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_ROW_PATTERN.match(line.strip()))


def _find_repeated_fragment_offsets(markdown_text: str, min_fragment_len: int = 60) -> list[int]:
    """Find character offsets of lines that reappear elsewhere in the document.

    Docling-rendered documents often repeat a page letterhead/footer (company
    name, address, registration number) at the start of a new logical section
    even when no markdown header marks that boundary - e.g. a new table
    "banner" row rather than a ##/### heading. A sufficiently long line that
    reappears elsewhere in the same document is a strong signal of a new
    section, independent of any specific company's letterhead text. Table
    separator rows are excluded since those legitimately repeat across
    unrelated tables without marking a section boundary.
    """
    lines = markdown_text.split("\n")
    seen: list[str] = []
    offsets: list[int] = []
    offset = 0

    for line in lines:
        if not _is_table_separator_row(line):
            fragment = re.sub(r"\s*\|\s*", " | ", line.strip(" |"))
            if len(fragment) >= min_fragment_len:
                if any(fragment in s or s in fragment for s in seen):
                    offsets.append(offset)
                seen.append(fragment)
        offset += len(line) + 1

    return offsets


def _split_header_sections(markdown_text: str) -> list[str]:
    """Split markdown into semantic sections on ##/### headers AND on repeated
    letterhead/banner fragments, so sections that aren't header-delimited
    (e.g. consecutive role tables with no markdown heading between them)
    still get separated instead of being merged into one oversized chunk.
    """
    boundaries = sorted(set(
        [m.start() for m in _HEADER_PATTERN.finditer(markdown_text)]
        + _find_repeated_fragment_offsets(markdown_text)
    ))

    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)

    sections = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(markdown_text)
        section = markdown_text[start:end].strip()
        if section:
            sections.append(section)
    return sections


_MAX_TABLE_GROUP_TOKENS = 400


def _table_aware_units(text: str, max_table_group_tokens: int = _MAX_TABLE_GROUP_TOKENS) -> list[str]:
    """Break text into paragraph units and token-budgeted table-row-group units.

    A large table is never returned as one atomic blob - it's split into row
    groups small enough to fit either parent or child chunk budgets, each
    group re-prefixed with the table's header + separator row so column
    meaning survives the split. A row is still never cut mid-way.
    """
    lines = text.split("\n")
    units: list[str] = []
    paragraph_buffer: list[str] = []
    table_buffer: list[str] = []
    in_table = False

    def flush_paragraph():
        if paragraph_buffer:
            units.append("\n".join(paragraph_buffer))
            paragraph_buffer.clear()

    def flush_table():
        if not table_buffer:
            return

        header_lines = table_buffer[:2]
        data_rows = table_buffer[2:]

        if not data_rows:
            units.append("\n".join(header_lines))
            table_buffer.clear()
            return

        header_tokens = count_tokens("\n".join(header_lines))
        group: list[str] = []
        group_tokens = header_tokens

        for row in data_rows:
            row_tokens = count_tokens(row)
            if group and group_tokens + row_tokens > max_table_group_tokens:
                units.append("\n".join(header_lines + group))
                group = []
                group_tokens = header_tokens
            group.append(row)
            group_tokens += row_tokens

        if group:
            units.append("\n".join(header_lines + group))

        table_buffer.clear()

    for line in lines:
        is_row = _is_table_row(line)
        if is_row:
            if not in_table:
                flush_paragraph()
            in_table = True
            table_buffer.append(line)
            continue

        if in_table:
            flush_table()
        in_table = False
        paragraph_buffer.append(line)
        if line.strip() == "":
            flush_paragraph()

    flush_table()
    flush_paragraph()

    return [u for u in units if u.strip()]


def _pack_units_by_tokens(units: list[str], chunk_size: int, chunk_overlap: int) -> list[str]:
    """Greedily pack units into token-budgeted chunks with trailing overlap."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = count_tokens(unit)
        if current and current_tokens + unit_tokens > chunk_size:
            chunks.append("\n".join(current))

            overlap_units: list[str] = []
            overlap_tokens = 0
            for u in reversed(current):
                t = count_tokens(u)
                if overlap_tokens + t > chunk_overlap:
                    break
                overlap_units.insert(0, u)
                overlap_tokens += t
            current = overlap_units
            current_tokens = overlap_tokens

        current.append(unit)
        current_tokens += unit_tokens

        if unit_tokens > chunk_size:
            # This single unit alone already exceeds the budget (an
            # unsplittable row-group bigger than chunk_size). Emit it on its
            # own immediately instead of letting more units pile on top.
            chunks.append("\n".join(current))
            current = []
            current_tokens = 0

    if current:
        chunks.append("\n".join(current))

    return chunks


def _new_chunk(text_content: str, metadata: dict) -> chunkObjectClass:
    """Clone the chunkObject template from settings instead of hand-building one."""
    return settings.chunkObject.model_copy(
        update={"text_content": text_content, "metadata": metadata},
        deep=True,
    )


def create_parent_chunk(batch_of_docs: list[dict]) -> List[chunkObjectClass]:
    parent_chunk_size = settings.chunkerSettings.parent_chunk_size
    parent_chunk_overlap = settings.chunkerSettings.parent_chunk_overlap

    result: List[chunkObjectClass] = []

    for document in batch_of_docs:
        text = document.get("text_content", "")

        for section in _split_header_sections(text):
            units = _table_aware_units(section)
            for chunk_text in _pack_units_by_tokens(units, parent_chunk_size, parent_chunk_overlap):
                result.append(_new_chunk(
                    text_content=chunk_text,
                    metadata={
                        "uuid": str(uuid.uuid4()),
                        "doc_name": document.get("doc_name"),
                        "author": document.get("author"),
                        "mode_of_parse": document.get("mode_of_parse"),
                        "tokens": count_tokens(chunk_text),
                        "chunk_type": "parent",
                        "parent_id": None,
                    },
                ))

    return result


def create_child_chunks(parent_chunks: List[chunkObjectClass]) -> List[chunkObjectClass]:
    child_chunk_size = settings.chunkerSettings.child_chunk_size
    child_chunk_overlap = settings.chunkerSettings.child_chunk_overlap

    result: List[chunkObjectClass] = []

    for parent in parent_chunks:
        units = _table_aware_units(parent.text_content)
        for chunk_text in _pack_units_by_tokens(units, child_chunk_size, child_chunk_overlap):
            result.append(_new_chunk(
                text_content=chunk_text,
                metadata={
                    "uuid": str(uuid.uuid4()),
                    "doc_name": parent.metadata.get("doc_name"),
                    "author": parent.metadata.get("author"),
                    "mode_of_parse": parent.metadata.get("mode_of_parse"),
                    "tokens": count_tokens(chunk_text),
                    "chunk_type": "child",
                    "parent_id": parent.metadata.get("uuid"),
                },
            ))

    return result


_db_reset_this_run = False


def _ensure_private_file(path: Path, mode: int = 0o600) -> None:
    """Create the file with owner-only permissions if it doesn't exist yet,
    and (re-)apply the mode either way - mirrors secure_json_dump in loader.py.
    Owner-only bits are honored on Linux/macOS; Windows uses NTFS ACLs instead
    so this doesn't provide the same guarantee there.
    """
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, mode)
        os.close(fd)
    os.chmod(path, mode)


def _get_db_connection() -> sqlite3.Connection:
    global _db_reset_this_run

    db_path = Path(settings.chunkerSettings.chunked_documents_save_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not _db_reset_this_run:
        db_path.unlink(missing_ok=True)
        _db_reset_this_run = True

    _ensure_private_file(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            uuid TEXT PRIMARY KEY,
            parent_id TEXT REFERENCES chunks(uuid),
            chunk_type TEXT NOT NULL CHECK (chunk_type IN ('parent', 'child')),
            text_content TEXT NOT NULL,
            tokens INTEGER,
            doc_name TEXT,
            author TEXT,
            mode_of_parse TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_parent_id ON chunks(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_chunk_type ON chunks(chunk_type)")
    return conn


def add_to_database(
    conn: sqlite3.Connection,
    parent_chunks: List[chunkObjectClass],
    child_chunks: List[chunkObjectClass],
) -> None:
    """Insert parent then child chunks so the self-referencing parent_id FK always resolves."""
    rows = [
        (
            chunk.metadata.get("uuid"),
            chunk.metadata.get("parent_id"),
            chunk.metadata.get("chunk_type"),
            chunk.text_content,
            chunk.metadata.get("tokens"),
            chunk.metadata.get("doc_name"),
            chunk.metadata.get("author"),
            chunk.metadata.get("mode_of_parse"),
        )
        for chunk in (*parent_chunks, *child_chunks)
    ]
    conn.executemany(
        """
        INSERT INTO chunks (uuid, parent_id, chunk_type, text_content, tokens, doc_name, author, mode_of_parse)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def get_stats(
    conn: sqlite3.Connection,
    parent_chunks_created: int,
    child_chunks_created: int,
    failed_batches: int = 0,
) -> dict:
    db_path = Path(settings.chunkerSettings.chunked_documents_save_path)

    db_records_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db_records_parent = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE chunk_type = 'parent'"
    ).fetchone()[0]
    db_records_child = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE chunk_type = 'child'"
    ).fetchone()[0]
    avg_tokens, min_tokens, max_tokens = conn.execute(
        "SELECT AVG(tokens), MIN(tokens), MAX(tokens) FROM chunks"
    ).fetchone()

    stats = {
        "elapsed_seconds": round(time.time() - start_time, 2),
        "parent_chunks_created": parent_chunks_created,
        "child_chunks_created": child_chunks_created,
        "total_chunks_created": parent_chunks_created + child_chunks_created,
        "failed_batches": failed_batches,
        "db_path": str(db_path),
        "db_file_size_bytes": db_path.stat().st_size if db_path.is_file() else 0,
        "db_records_total": db_records_total,
        "db_records_parent": db_records_parent,
        "db_records_child": db_records_child,
        "avg_tokens_per_chunk": round(avg_tokens, 1) if avg_tokens is not None else None,
        "min_tokens_per_chunk": min_tokens,
        "max_tokens_per_chunk": max_tokens,
    }

    print(f"============ chunking run stats ===================")
    for key, value in stats.items():
        print(f"[{_lap()}]{indentation} {key}: {value}")

    return stats


def chunk_documents(batch_size: int):
    offset = 0
    json_path = access_document()

    if json_path is None:
        print(f"[{_lap()}]{indentation} ERROR - no source json found, aborting")
        return

    total_parent_chunks = 0
    total_child_chunks = 0
    failed_batches = 0

    conn = _get_db_connection()
    try:
        while True:
            batch, offset = get_json_batch(json_path, offset, batch_size)
            if not batch:
                break
            print(f"[{_lap()}]{indentation} New Batch Arrived of {len(batch)} - offset: {offset}")

            parent_chunks = create_parent_chunk(batch)
            child_chunks = create_child_chunks(parent_chunks)

            try:
                add_to_database(conn, parent_chunks, child_chunks)
                total_parent_chunks += len(parent_chunks)
                total_child_chunks += len(child_chunks)

            except Exception as e:
                failed_batches += 1
                print(f"[{_lap()}]{indentation} ERROR - Couldnt add the Chunks for {len(batch)} - offset: {offset} - {e}")

            print(f"[{_lap()}]{indentation} {len(parent_chunks)} parent chunks -> {len(child_chunks)} child chunks")

        get_stats(conn, total_parent_chunks, total_child_chunks, failed_batches)
    finally:
        conn.close()

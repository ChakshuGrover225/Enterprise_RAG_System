from __future__ import annotations

import os
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

indentation = '--- --- '*2
import time
start_time = time.time()
def _lap() -> str:
    return f"{time.time() - start_time:.2f}s"


from pathlib import Path
import json
import sqlite3
from typing import TYPE_CHECKING

from huggingface_hub import snapshot_download

from scripts.backend.backend_settings import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


def fetch_chunks(limit: int = None) -> list[sqlite3.Row]:
    db_path = Path(settings.chunkerSettings.chunked_documents_save_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT * FROM chunks"
        if limit is not None:
            query += " LIMIT ?"
            return conn.execute(query, (limit,)).fetchall()
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def print_value(limit):
    for row in fetch_chunks(limit):
        print(dict(row))




def initiate_embedding_logic() -> Path:
    
    embedding_model_name: str = settings.embedderSettings.embedding_model_name
    embedding_model_save_path: str = settings.embedderSettings.embedding_model_save_path
    
    model_path = Path(embedding_model_save_path) / embedding_model_name

    if model_path.is_dir() and any(model_path.iterdir()):
        return model_path

    snapshot_download(
        repo_id=f"sentence-transformers/{embedding_model_name}",
        local_dir=model_path,
    )
    print(f"[{_lap()}]{indentation}EMBEDDER MODEL DOWNLOADED AT {model_path}")

    return model_path


def embed_document(input_string: str, model: SentenceTransformer) -> list[float] | None:
    try:
        return model.encode(input_string).tolist()
    except Exception as e:
        print(f"[{_lap()}]{indentation} ERROR - couldn't embed document: {e}")
        return None

def embed_document_batch(texts: list[str], model: SentenceTransformer) -> list[list[float] | None]:
    try:
        vectors = model.encode(texts)
        return [vector.tolist() for vector in vectors]
    except Exception as e:
        print(f"[{_lap()}]{indentation} ERROR - couldn't embed batch of {len(texts)}: {e}")
        return [None] * len(texts)


def embed_chunks(chunk_batch_size: int, model: SentenceTransformer) -> None:
    db_path = Path(settings.chunkerSettings.chunked_documents_save_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        for column in ("embedding_vector", "embedding_vector_name"):
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE chunks ADD COLUMN {column} TEXT")

        offset = 0
        while True:
            rows = conn.execute(
                "SELECT uuid, text_content FROM chunks WHERE chunk_type = 'child' ORDER BY uuid LIMIT ? OFFSET ?",
                (chunk_batch_size, offset),
            ).fetchall()
            if not rows:
                break

            for row in rows:
                embedding_vector = embed_document(row["text_content"], model)
                conn.execute(
                    "UPDATE chunks SET embedding_vector = ?, embedding_vector_name = ? WHERE uuid = ?",
                    (
                        json.dumps(embedding_vector) if embedding_vector is not None else None,
                        settings.embedderSettings.embedding_model_name if embedding_vector is not None else None,
                        row["uuid"],
                    ),
                )
            conn.commit()
            print(f"[{_lap()}]{indentation} embedded {len(rows)} chunks (offset {offset})")

            offset += len(rows)
    finally:
        conn.close()


def embed_documents(chunk_batch_size : int ):
    from sentence_transformers import SentenceTransformer

    model_path = initiate_embedding_logic()

    try:
        embedding_model = SentenceTransformer(str(model_path))
        print(f"[{_lap()}]{indentation}LOADED MODEL {settings.embedderSettings.embedding_model_name}")
    except Exception as e:
        print(f"[{_lap()}]{indentation}ERROR loading {settings.embedderSettings.embedding_model_name}: {e}")
        return

    embed_chunks(chunk_batch_size, embedding_model)
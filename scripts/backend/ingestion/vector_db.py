indentation = '--- --- '*2
import time
start_time = time.time()
def _lap() -> str:
    return f"{time.time() - start_time:.2f}s"


from scripts.backend.backend_settings import settings
from pathlib import Path
import json
import sqlite3
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

def initialise_vector_db() -> Path:

    vector_db_path = Path(settings.vectordbSettings.vector_db_save_path)
    already_present = vector_db_path.exists() and any(vector_db_path.iterdir())

    try:
        # QdrantClient(path=...) opens the on-disk store at vector_db_path,
        # creating it (and any missing parent dirs) the first time it's called.
        QdrantClient(path=str(vector_db_path)).close()
        print(f"[{_lap()}]{indentation}VECTOR DB {'FOUND' if already_present else 'CREATED'} AT {vector_db_path}")

    except Exception as e:
        print(f"[{_lap()}]{indentation}ERROR SETTING UP VECTOR DB {e}")
        return None

    return vector_db_path


def add_chunks_to_vector() -> dict:
    """Reads 'child' chunks out of chunked_document.db and upserts the ones
    that already have an embedding_vector into the vector DB's collection.
    """
    total_record_count = 0
    added_record_count = 0

    db_path = Path(settings.chunkerSettings.chunked_documents_save_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE chunk_type = 'child'"
        ).fetchall()
        total_record_count = len(rows)

        points = []
        for row in rows:
            if row["embedding_vector"] is None:
                continue
            points.append(PointStruct(
                id=row["uuid"],
                vector=json.loads(row["embedding_vector"]),
                payload={
                    "text_content": row["text_content"],
                    "doc_name": row["doc_name"],
                    "author": row["author"],
                    "mode_of_parse": row["mode_of_parse"],
                    "tokens": row["tokens"],
                    "parent_id": row["parent_id"],
                },
            ))

        if points:
            vector_db_path = initialise_vector_db()
            client = QdrantClient(path=str(vector_db_path))
            try:
                collection_name = settings.vectordbSettings.collection_name
                if not client.collection_exists(collection_name):
                    client.create_collection(
                        collection_name=collection_name,
                        # DOT matches multi-qa-mpnet-base-dot-v1, the dot-product-trained model in embedderSettings
                        vectors_config=VectorParams(size=len(points[0].vector), distance=Distance.DOT),
                    )
                client.upsert(collection_name=collection_name, points=points)
                added_record_count = len(points)
            finally:
                client.close()

        print(f"[{_lap()}]{indentation}VECTOR DB: found {total_record_count} child chunks, added {added_record_count}")

    except Exception as e:
        print(f"[{_lap()}]{indentation}ERROR ADDING CHUNKS TO VECTOR DB {e}")
    finally:
        conn.close()

    return {
        "total_record_count": total_record_count,
        "added_record_count": added_record_count,
    }


def add_to_vector_db():
    
    

    vector_setup_response = initialise_vector_db()
    print(f"---------------------------------- {vector_setup_response}")
    ingestion_stats = add_chunks_to_vector()




    return ingestion_stats
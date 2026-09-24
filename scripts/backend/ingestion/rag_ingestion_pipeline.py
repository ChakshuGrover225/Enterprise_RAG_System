import scripts.backend.ingestion.chunker as chunker
import scripts.backend.ingestion.loader as loader
import scripts.backend.ingestion.embedder as embedder
import scripts.backend.ingestion.preprocess as preprocess

# ------ libraries ------------



# ------- helper functions -------------


# -------- code execution ---------------
def run_rag_ingestion_pipeline():

    try:
        loader.load_documents()
        chunker.chunk_documents()
        preprocess.preprocess_chunks()
        embedder.embed_chunks()

        print(f"{'\t'*2}ingestion happened successfully.")

    except Exception as e:
        print(f"{'\t'*2}ingestion did not work")

    finally:
        print(f"{'\t'*2}Ingestion process Ends")
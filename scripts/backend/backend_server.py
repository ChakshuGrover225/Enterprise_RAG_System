import scripts.backend.ingestion.rag_ingestion_pipeline as rag_ingestion_pipeline


# Fast api server starts here:
def run_backend():

    print("you reached the script/backend_server.py")
    rag_ingestion_pipeline.run_rag_ingestion_pipeline()



print("entered rag ingesition pipeline")

indentation = '--- --- '*1
from scripts.backend.ingestion import loader, chunker, embedder, vector_db
from scripts.backend import execution_statistics
import time

# start execution_stats.py here
execution_statistics.start()




# load documents

try:
    loader.load_document()
    print(f"{indentation}Loaded document SUCCESSFULLY")
    pass

except Exception as e:
    print(f"{indentation}error in Loader.load_document {e}")

# -----------------------------------------------------



# chunk documents
try:
    print()
    chunker.chunk_documents(batch_size = 5)
    
    
except Exception as e:
    print(f"{indentation}failed Chunking {e}")
 



#embed document
try:
    embedder.embed_documents(  chunk_batch_size= 30 )

except Exception as e:
    print()




#add chunks in vector Database
try:
    vector_db_stats = vector_db.add_to_vector_db()
    print(f"{indentation} Added: {vector_db_stats['added_record_count']}   total: {vector_db_stats['total_record_count']} ")
    pass
except Exception as e:
    print()
# present logs





# print stats from execution_stats.py here in last
execution_statistics.report()
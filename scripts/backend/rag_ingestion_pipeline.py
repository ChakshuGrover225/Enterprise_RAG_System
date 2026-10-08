print("entered rag ingesition pipeline")

indentation = '--- --- '*1
from scripts.backend.ingestion import loader, chunker, embedder
from scripts.backend import execution_statistics
import time

# start execution_stats.py here
execution_statistics.start()



'''

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
 
'''

#preprocess documents
try:
    pass
except Exception as e:
    print()
#embed document
try:
    embedder.embed_documents(  chunk_batch_size= 30 )
    pass #         embedder.print_value(2)
except Exception as e:
    print()








'''

#add chunks in vector Database
try:
    pass
except Exception as e:
    print()
# present logs



'''


# print stats from execution_stats.py here in last
execution_statistics.report()
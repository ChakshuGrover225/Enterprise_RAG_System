print("entered rag ingesition pipeline")


indentation = '--- --- '*1
from scripts.backend.ingestion import loader, chunker, embedder
import time


# load documents

try:
    #loader.load_document()
    #print(f"{indentation}Loaded document SUCCESSFULLY")
    pass

except Exception as e:
    pass
    #print(f"{indentation}error in Loader.load_document {e}")

# -----------------------------------------------------



# chunk documents
try:
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
    pass
except Exception as e:
    print()
#add chunks in vector Database
try:
    pass
except Exception as e:
    print()
# present logs


'''

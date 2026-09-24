# --------- libraries ------------------
from multiprocessing import Process
from scripts.backend import backend_server
from scripts.frontend import frontend_server


# --------- helper function -----------
def run_backend():
    try:
        backend_server.run_backend()         # start scripts/backend_server

    except Exception as e:
        print(f"Error starting Backend, error: {e}")





def run_frontend():

    try:
        frontend_server.intro()

    except Exception as e:
            print(f"Error starting frontedn, error: {e}")

    # start scripts/frontend_server





# -------- Code execution --------------
def start_rag_application():
    # write the code here
    run_backend()
    run_frontend()


start_rag_application()
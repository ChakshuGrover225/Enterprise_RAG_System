from pydantic import BaseModel


class loaderSettingClass(BaseModel):
    database_file_Path:str = r"F:\project\Portofolio Projects\RAG in Production\Enterprise_RAG_System\input_backend_data\data_source"
    acceptable_file_agreement: list[str] = ['pdf', 'xlsx', 'docx', 'txt', 'md']
    json_save_path : str = r"scripts/backend/ingestion/output/loaded_document.json"


class chunkObjectClass(BaseModel):
    text_content: str = None
    metadata: dict = {
        "uuid" : None,
        "doc_name" : None,
        "author" : None,
        "mode_of_parse" : None,
        "tokens" : None,
        "chunk_type" : None,
        "parent_id" : None

    }


class chunkerSettingClass(BaseModel):
    json_saved_path : str = loaderSettingClass().json_save_path

    chunked_documents_save_path : str = r"scripts/backend/ingestion/output/chunked_document.db"

    parent_chunk_size: int = 3000
    parent_chunk_overlap: int = 500

    child_chunk_size: int = 500
    child_chunk_overlap: int = 150



class backendSettingClass(BaseModel):
    loaderSettings: loaderSettingClass = loaderSettingClass()
    chunkerSettings: chunkerSettingClass = chunkerSettingClass()
    chunkObject: chunkObjectClass = chunkObjectClass()


settings = backendSettingClass()





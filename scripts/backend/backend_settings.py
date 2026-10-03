from pydantic import BaseModel


class loaderSettingClass(BaseModel):
    database_file_Path:str = r"F:\project\Portofolio Projects\RAG in Production\Enterprise_RAG_System\input_backend_data\data_source"
    acceptable_file_agreement: list[str] = ['pdf', 'xlsx', 'docx', 'txt', 'md']
    json_save_path : str = r"scripts/backend/ingestion/output/loaded_document.json"

class backendSettingClass(BaseModel):
    loaderSettings: loaderSettingClass = loaderSettingClass()

settings = backendSettingClass()




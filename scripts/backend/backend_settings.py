from pydantic import BaseModel

class LoadDataClass(BaseModel):
    text_content : str = ""
    metadata : dict = {}


class LoaderSettingClass():
    path_to_local_file_storage = r'input_backend_data\data_source'



class SettingClass():
    loader_setting = LoaderSettingClass()


settings = SettingClass()


print(f"{'\t'*4}settings import right in Package")
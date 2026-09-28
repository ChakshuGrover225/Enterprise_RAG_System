from langchain_groq import ChatGroq
from langchain_core.language_models.chat_models import BaseChatModel
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from dotenv import load_dotenv
import os


import json
from prompt_inventory import generate_section_prompt

context = ""


import json
import re

def print_final_response_beautifully(response_json_text: str) -> None:
    response = json.loads(response_json_text, strict=False)

    ansi_bold_pattern = re.compile(r"\*(.+?)\*")
    ansi_italic_pattern = re.compile(r"_(.+?)_")

    formatted_text = ansi_bold_pattern.sub(r"\033[1m\1\033[0m", response["final_response"])
    formatted_text = ansi_italic_pattern.sub(r"\033[3m\1\033[0m", formatted_text)

    print(f"\033[4m{response['name']}\033[0m\n")
    print(formatted_text)






def maintain_context(context, response):
    return (context + '-'*20 + response)

def generate_section(context, llm, input_query, prompt) -> str:
    filled_prompt = prompt.format(context= context, input_query=input_query)
    llm_response = llm.invoke(filled_prompt)
    return llm_response.content



load_dotenv()
groq_api_key = os.getenv("groq_api_key")

print("none")


input_query = "Write me an article on how to make a icecream.  "

print(input_query)

prompt1 = generate_section_prompt.base_prompt
prompt2 = generate_section_prompt.article_generator
prompt3 = generate_section_prompt.editorial_prompt




llm_model = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=groq_api_key,
    temperature=0
)



response1 = generate_section(  context= context, 
                                llm= llm_model, 
                                input_query= input_query, 
                                prompt= prompt1 
                            )


context = maintain_context(context, response1)

print(response1)

response2 = generate_section(  context= context, 
                                llm= llm_model, 
                                input_query= response1, 
                                prompt= prompt2 
                            )






#print(response2)

context = maintain_context(context, response2)


response3 = generate_section(
        llm=llm_model,
        context = context,
        prompt = prompt3,
        input_query=response2
)

#print(response3)




print_final_response_beautifully(response3)






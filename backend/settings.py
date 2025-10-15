import os
from pydantic import BaseSettings

class Settings(BaseSettings):
    openai_api_key: str
    model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    index_path: str = os.path.join(os.path.dirname(__file__), "..", "storage")
    index_name: str = "isabel_faiss"

    class Config:
        env_prefix = "OPENAI_"
        env_file = os.path.join(os.path.dirname(__file__), "..", ".env")

settings = Settings(openai_api_key=os.getenv("OPENAI_API_KEY", ""))

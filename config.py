from dotenv import load_dotenv
load_dotenv()
import os

class Settings:
    # llm
    MODEL = "deepseek-flash"
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

    # 高德地图
    AMAP_API_KEY = os.getenv("AMAP_API_KEY")
    AMAP_BASE_URL = os.getenv("AMAP_BASE_URL")

    # 向量模型
    EMBEDDING_MODEL = "text-embedding-v3"
    EMBEDDING_DIM=1024
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
    DASHSCOPE_BASE_URL = os.getenv("DASHSCOPE_BASE_URL")

    # Milvus
    MILVUS_URI = os.getenv("MILVUS_URI")
    MILVUS_TOKEN = os.getenv("MILVUS_TOKEN")

    # Redis
    REDIS_URL = os.getenv("REDIS_URL")

    # PostgreSOL
    POSTGRE_URI=os.getenv("POSTGRE_JSEP")

    # rerank模型
    RERANK_MODEL="qwen3.7-text-rerank"
    RERANK_BASE_URL=os.getenv("RERANK_BASE_URL")

settings = Settings()
from functools import cache
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from config import settings


@cache
def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=0.2,
        streaming=True,
        timeout=60,
        max_retries=0,
    )

@cache
def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        dimensions=settings.EMBEDDING_DIM,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=settings.DASHSCOPE_BASE_URL,
        check_embedding_ctx_length=False,
        chunk_size=10,
    )
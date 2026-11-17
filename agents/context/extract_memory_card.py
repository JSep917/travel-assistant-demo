from agents.context.store import get_memory_store
from langchain_core.messages import SystemMessage
from common.model import get_llm, get_embeddings
from common.pydantic_model import MemoryCard
from common.call_with_retry import call_with_retry
import hashlib
import time

async def extract_memory_card(human_text: str, ai_text: str) -> MemoryCard:
    """抽取记忆卡片"""
    messages = [
        SystemMessage(content=(
            f"""
            只输出 JSON 结构化结果: 
            topic: 本轮主题
            summary: 100 字以内摘要
            keywords: 最多 5 个检索关键词
            
            用户消息: {human_text}
            助手消息: {ai_text}
            """
        ))
    ]
    llm_with_schema = get_llm().with_structured_output(
        schema=MemoryCard,
        method="json_mode"
    )
    try:
        response: MemoryCard = await call_with_retry(lambda: llm_with_schema.ainvoke(messages))
    except Exception as e:
        print(f"记忆卡片抽取失败: {e}")
        raise

    return response


def _build_memory_id(thread_id: str, user_id: str, human_text: str) -> str:
    """幂等写入"""
    digest = hashlib.sha256(human_text.encode("utf-8")).hexdigest()[:32]
    return f"{thread_id}:{user_id}:{digest}"


async def write_in(thread_id: str, user_id: str, truns: list[tuple[str, str]]) -> int:
    """批量对话入库"""
    store = get_memory_store()
    existing_ids = set(store.list_memory_ids(user_id, thread_id))
    seen_ids = set()
    raw_rows: list[tuple[str, str]] = []
    rows = []

    if not truns:
        return 0

    for human_text, ai_text in truns:
        if not human_text or not ai_text:
            continue

        memory_id = _build_memory_id(thread_id, user_id, human_text)
        if memory_id in seen_ids or memory_id in existing_ids:
            continue

        content = f"用户: {human_text.strip()}\n助手: {ai_text.strip()}"
        try:
            memory_card: MemoryCard = await extract_memory_card(human_text, ai_text)
        except Exception:
            memory_card = None

        if not isinstance(memory_card, MemoryCard) or memory_card.is_empty():
            continue

        seen_ids.add(memory_id)
        raw_rows.append((memory_id, content, memory_card.to_context()))

    if not raw_rows:
        return 0

    vectors = await get_embeddings().aembed_documents([content for _, content, _ in raw_rows])

    for vector, (memory_id, content, memory_card) in zip(vectors, raw_rows):
        rows.append({
            "memory_id": memory_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "content": content,
            "memory_card": memory_card,
            "created_at": int(time.time() * 1000),
            "dense": vector,
        })

    for index in range(0, len(rows), 100):
        store.upsert(rows[index:index + 100])

    return len(rows)



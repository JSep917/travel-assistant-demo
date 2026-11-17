import asyncio

async def call_with_retry(fn, attempts: int = 3):
    """LLM调用等待重试"""
    last_error = None

    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as e:
            last_error = e
            await asyncio.sleep(attempt + 1)

    raise last_error
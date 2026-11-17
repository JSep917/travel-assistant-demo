import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from config import settings
from services.chat.chat_service import ChatService
from services.db.checkpoint_manager import CheckpointManager
from services.db.thread_repository import ThreadRepository
from services.db.user_pofile_repository import UserProfileRepository
from services.redis.tool_cache import close_tool_cache
from agents.main_graph.graph import build_main_graph
from agents.context.store import close_memory_store


async def print_events(events):
    """打印事件，返回 (answer, interrupt_payload)。"""
    answer = ""
    printed_token = False
    interrupt_payload = None

    async for event in events:
        kind = event.get("event")

        if kind == "token":
            if not printed_token:
                print("助手：", end="", flush=True)
                printed_token = True

            print(event.get("content", ""), end="", flush=True)

        elif kind == "done":
            if not printed_token:
                print(f"助手：{event.get('answer', '')}")
            else:
                print()

            answer = event.get("answer", "")

        elif kind == "interrupt":
            interrupt_payload = event.get("payload") or {}

        elif kind in {
            "subagent", "router", "supervisor",
            "planning", "respond", "answer",
            "error",
        }:
            print(
                f"[{kind}] "
                f"{event.get('message') or event.get('target') or ''}",
                flush=True,
            )

    return answer, interrupt_payload


async def main() -> None:
    pool = AsyncConnectionPool(
        conninfo=settings.POSTGRE_URI,
        min_size=1,
        max_size=5,
        open=False,
    )
    await pool.open()

    try:
        async with AsyncPostgresSaver.from_conn_string(
            settings.POSTGRE_URI
        ) as checkpointer:
            manager = CheckpointManager(checkpointer)
            await manager.setup()

            service = ChatService(
                thread_repo=ThreadRepository(pool),
                profile_repo=UserProfileRepository(pool),
                graph=await build_main_graph(checkpointer),
            )

            thread_id = "demo-user:a3b0d765"
            user_id = "test-user"
            question = input("你：")

            answer, interrupt_payload = await print_events(
                service.stream_chat(
                    user_id=user_id,
                    thread_id=thread_id,
                    question=question,
                ),
            )

            for _ in range(2):
                if not interrupt_payload:
                    break

                print("\n需要补充信息：")

                for item in interrupt_payload.get("questions", []):
                    print(f"- {item.get('question')}")
                    field = item.get("field")

                    answer = input(f"请输入（回车跳过）：").strip()

                    resume = {"action": "skip"} if not answer else {field: answer}


                # 断点续跑
                answer, interrupt_payload = await print_events(
                    service.resume_chat(
                        user_id=user_id,
                        thread_id=thread_id,
                        resume=resume,
                    ),
                )

            await service.wait_background()
    finally:
        # 关闭依赖
        await close_memory_store()
        await close_tool_cache()
        await pool.close()


if __name__ == "__main__":
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    asyncio.run(main())

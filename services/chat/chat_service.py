import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
)
from langgraph.types import Command

from agents.context.extract_user_profile import update_profile
from services.db.thread_repository import ThreadRepository
from services.db.user_pofile_repository import UserProfileRepository



@dataclass
class _TurnResult:
    """一轮流式执行结果"""
    thread_id: str = ""
    user_id: str = ""
    preview: str = ""
    answer: str = ""
    interrupted: bool = False
    failed: bool = False
    cancelled: bool = False
    config: dict = field(default_factory=dict)
    question: str = ""
    parts: list[str] = field(default_factory=list)
    buffer: list[dict] = field(default_factory=list)


class ChatService:
    """流式输出 + 后台任务"""
    def __init__(
        self,
        thread_repo: ThreadRepository,
        profile_repo: UserProfileRepository,
        graph,
    ):
        self.thread_repo = thread_repo
        self.profile_repo = profile_repo
        self.graph = graph
        self._tasks: set[asyncio.Task] = set()

    async def stream_chat(
        self,
        user_id: str,
        thread_id: str,
        question: str,
    ) -> AsyncIterator[dict[str, Any]]:
        thread_id = thread_id or f"{user_id}:{uuid4().hex[:8]}"
        result = _TurnResult(
            thread_id=thread_id,
            user_id=user_id,
            preview=question,
            question=question,
        )

        async for event in self._stream_turn(
            graph_input={"messages": [HumanMessage(content=question)]},
            thread_id=thread_id,
            user_id=user_id,
            result=result,
        ):
            yield event

        if result.interrupted or result.failed:
            return

        await self._background_tasks(result)

        yield {
            "event": "done",
            "answer": result.answer,
            "cancelled": result.cancelled,
        }

    async def resume_chat(
        self,
        user_id: str,
        thread_id: str,
        resume: dict,
    ) -> AsyncIterator[dict[str, Any]]:
        result = _TurnResult(
            thread_id=thread_id,
            user_id=user_id,
        )

        async for event in self._stream_turn(
            graph_input=Command(resume=resume),
            thread_id=thread_id,
            user_id=user_id,
            result=result,
        ):
            yield event

        if result.interrupted or result.failed:
            return

        await self._background_tasks(result)

        yield {
            "event": "done",
            "answer": result.answer,
            "cancelled": result.cancelled,
        }

    async def _stream_turn(
        self,
        graph_input: dict | Command,
        thread_id: str,
        user_id: str,
        result: _TurnResult,
    ) -> AsyncIterator[dict[str, Any]]:
        thread = await self.thread_repo.get(thread_id, user_id)

        if thread is None:
            await self.thread_repo.create(
                thread_id=thread_id,
                user_id=user_id,
                title="新会话",
            )

        user_profile = await self.profile_repo.get(user_id) or {}
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
                "user_profile": user_profile,
            }
        }
        result.config = config

        yield {
            "event": "meta",
            "thread_id": thread_id,
            "user_id": user_id,
        }

        try:
            async for mode, chunk in self.graph.astream(
                graph_input,
                config=config,
                stream_mode=["custom", "messages", "updates"],
            ):
                if mode == "custom":
                    yield chunk or {}

                elif mode == "messages":
                    token, metadata = chunk

                    if metadata.get("langgraph_node") not in {
                        "answer",
                        "respond",
                    }:
                        continue

                    if not isinstance(token, AIMessageChunk):
                        continue

                    text = str(token.content or "")

                    if not text:
                        continue

                    result.parts.append(text)

                    yield {
                        "event": "token",
                        "content": text,
                    }

                elif mode == "updates":
                    if isinstance(chunk, dict) and "__interrupt__" in chunk:
                        interrupts = chunk["__interrupt__"]
                        result.interrupted = True

                        yield {
                            "event": "interrupt",
                            "payload": (
                                interrupts[0].value
                                if interrupts
                                else {}
                            ),
                        }
                        break

                    for update in chunk.values():
                        if not isinstance(update, dict):
                            continue

                        for item in update.get("messages", []):
                            if (
                                isinstance(item, AIMessage)
                                and item.content
                            ):
                                result.answer = str(item.content)

                                if getattr(item, "name", None) == "clarify_cancel":
                                    result.cancelled = True

                        if update.get("planning"):
                            yield {
                                "event": "plan",
                                "planning": update["planning"],
                            }

        except Exception as e:
            result.failed = True

            yield {
                "event": "error",
                "message": str(e),
            }
            return

        if result.interrupted:
            return

        result.answer = "".join(result.parts) or result.answer

        try:
            snapshot = await self.graph.aget_state(config)
            values = snapshot.values
            result.buffer = values.get("buffer") or []

            if not result.question:
                for item in reversed(values.get("messages", [])):
                    if isinstance(item, HumanMessage):
                        result.question = str(item.content or "")
                        break
        except Exception:
            pass

    async def _background_tasks(self, result: _TurnResult) -> None:
        try:
            await self.thread_repo.touch_thread(
                thread_id=result.thread_id,
                user_id=result.user_id,
                preview=result.answer or result.preview,
            )
        except Exception as e:
            print(f"更新会话预览失败: {e}")

        buffer = list(result.buffer or [])

        if result.question and result.answer:
            buffer.append({
                "user_question": result.question,
                "ai_answer": result.answer,
            })

        await self.graph.aupdate_state(
            result.config,
            {"buffer": buffer},
        )
        result.buffer = buffer

        if len(buffer) >= 5:
            # 5 轮对话后抽取用户偏好
            self._spawn(
                self._update_profile_task(result)
            )

    async def _update_profile_task(self, result: _TurnResult) -> None:
        updated = await update_profile(
            buffer=result.buffer,
            repo=self.profile_repo,
            config=result.config,
        )

        if updated is None:
            return

        await self.graph.aupdate_state(
            result.config,
            {"buffer": None},
        )

    async def wait_background(self) -> None:
        """等待本轮后台任务结束，测试和进程退出前使用。"""
        if not self._tasks:
            return

        await asyncio.gather(
            *self._tasks,
            return_exceptions=True,
        )

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(completed: asyncio.Task) -> None:
            self._tasks.discard(completed)

        task.add_done_callback(done)

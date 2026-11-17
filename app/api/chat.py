import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_chat_service, get_user_id
from services.chat.chat_service import ChatService

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    thread_id: str | None = Field(
        default=None,
        description="会话 ID，不传则新建会话",
    )
    message: str = Field(
        min_length=1,
        max_length=2000,
        description="用户消息",
    )

class ResumeRequest(BaseModel):
    thread_id: str = Field(description="会话 ID")
    resume: dict[str, Any] = Field(
        description="interrupt 恢复数据",
    )

class ChatResponse(BaseModel):
    thread_id: str = Field(description="会话 ID")
    answer: str = Field(description="助手回答")

def sse(event: dict) -> str:
    return (
        "data: "
        + json.dumps(event, ensure_ascii=False, default=str)
        + "\n\n"
    )

async def event_stream(events) -> Any:
    try:
        async for event in events:
            yield sse(event)
    except Exception as exc:
        yield sse({
            "event": "error",
            "message": str(exc),
        })

def stream_response(events) -> StreamingResponse:
    return StreamingResponse(
        event_stream(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@router.post(
    "/chat/stream",
    summary="流式发送消息",
    description="通过 SSE 逐 token 返回助手回答。",
)
async def chat_stream(
    body: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    user_id: str = Depends(get_user_id),
) -> StreamingResponse:
    thread_id = body.thread_id or f"{user_id}:{uuid4().hex[:8]}"

    return stream_response(
        service.stream_chat(
            user_id=user_id,
            thread_id=thread_id,
            question=body.message,
        )
    )

@router.post(
    "/chat/resume",
    summary="恢复被 interrupt 的对话",
)
async def chat_resume(
    body: ResumeRequest,
    service: ChatService = Depends(get_chat_service),
    user_id: str = Depends(get_user_id),
) -> StreamingResponse:
    return stream_response(
        service.resume_chat(
            user_id=user_id,
            thread_id=body.thread_id,
            resume=body.resume,
        )
    )

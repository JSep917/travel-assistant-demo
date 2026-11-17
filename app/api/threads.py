from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from app.api.deps import get_user_id
from agents.context.store import get_memory_store

router = APIRouter(prefix="/api", tags=["threads"])


class ThreadCreate(BaseModel):
    thread_id: str | None = None
    title: str = Field(default="新会话", max_length=50)


class ThreadRename(BaseModel):
    title: str = Field(min_length=1, max_length=50)


def get_repo(request: Request):
    return request.app.state.thread_repo


def get_graph(request: Request):
    return request.app.state.graph


def get_checkpoint_manager(request: Request):
    return request.app.state.checkpoint_manager


@router.get("/threads")
async def list_threads(
    request: Request,
    user_id: str = Depends(get_user_id),
):
    repo = get_repo(request)
    rows = await repo.list_by_user(user_id)
    return {"items": rows}


@router.post("/threads")
async def create_thread(
    body: ThreadCreate,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    repo = get_repo(request)
    thread_id = body.thread_id or f"{user_id}:{uuid4().hex[:8]}"

    row = await repo.create(
        thread_id=thread_id,
        user_id=user_id,
        title=body.title,
    )
    return row


@router.patch("/threads/{thread_id}")
async def rename_thread(
    thread_id: str,
    body: ThreadRename,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    repo = get_repo(request)
    ok = await repo.rename(thread_id, user_id, body.title)

    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")

    return {"ok": True}


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    repo = get_repo(request)
    ok = await repo.delete(thread_id, user_id)

    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")

    await get_checkpoint_manager(request).delete_thread(thread_id)

    try:
        get_memory_store().delete_by_thread(thread_id)
    except Exception:
        pass

    return {"ok": True}


@router.get("/threads/{thread_id}/messages")
async def thread_messages(
    thread_id: str,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    repo = get_repo(request)

    if await repo.get(thread_id, user_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    graph = get_graph(request)
    state = await graph.aget_state({
        "configurable": {"thread_id": thread_id}
    })

    items = []
    subagents = {"geo_agent", "poi_agent", "route_agent"}

    for message in state.values.get("messages", []):
        if isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            if getattr(message, "name", None) in subagents:
                continue
            role = "assistant"
        else:
            continue

        content = str(message.content or "")

        if not content.strip():
            continue

        name = getattr(message, "name", None)

        if name == "clarify_cancel":
            name = None

        items.append({
            "role": role,
            "content": content,
            "name": name,
        })

    return {"items": items}

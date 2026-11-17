from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class MainGraph(TypedDict):
    """主图状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    current_task: dict | None
    planning: dict | None
    user_input: dict[str, str] | None
    clarify_answers: dict[str, str] | None
    clarify_question: list | None
    results: dict[str, dict] | None
    buffer: list[dict] | None
    need_memory: bool | None
    context: list[BaseMessage] | None
    next_node: str | None


class GeoAgent(TypedDict):
    """Geo Agent 状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    task_result: dict | None
    execute_retry: int | None
    extract_retry: int | None


class PoiAgent(TypedDict):
    """Poi Agent 状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    task_result: dict | None
    execute_retry: int | None
    extract_retry: int | None


class RouteAgent(TypedDict):
    """Route Agent 状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    task_result: dict | None
    execute_retry: int | None
    extract_retry: int | None

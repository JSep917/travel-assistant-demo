from langchain_core.messages import HumanMessage, ToolMessage
import json

KEEP_RECENT_TURNS = 2
SUMMARY_BATCH_TURNS = 2


def completed_task_ids(planning: dict) -> set[str]:
    """已完成的子任务 ID。"""
    return {
        task["task_id"]
        for task in planning.get("tasks", [])
        if task.get("status") == "completed"
    }


def ready_tasks(planning: dict) -> list[dict]:
    """依赖已满足、可以执行的子任务。"""
    completed = completed_task_ids(planning)

    return [
        task
        for task in planning.get("tasks", [])
        if task.get("status") == "pending"
        and all(
            dependency in completed
            for dependency in task.get("depends_on", [])
        )
    ]


def mark_task_status(planning: dict, task_id: str, status: str) -> dict:
    """复制计划并把指定任务改成新状态。"""
    return {
        **planning,
        "tasks": [
            {**task, "status": status}
            if task["task_id"] == task_id
            else task
            for task in planning.get("tasks", [])
        ],
    }


def split_turns(messages: list) -> list[list]:
    """按用户消息把消息切成轮次。"""
    turns = []

    for message in messages:
        if isinstance(message, HumanMessage):
            turns.append([])

        if turns:
            turns[-1].append(message)

    return turns


def flatten_turns(turns: list[list]) -> list:
    """把轮次列表压回消息列表。"""
    return [
        message
        for turn in turns
        for message in turn
    ]


def compact_messages(messages: list, max_turns: int) -> list:
    """只保留最近 N 轮。"""
    return flatten_turns(split_turns(messages)[-max_turns:])


def messages_after_turns(messages: list, summarized_turns: int) -> list:
    """跳过已经摘要过的轮次，保留后面的消息。"""
    return flatten_turns(split_turns(messages)[summarized_turns:])


def latest_human_message(messages: list) -> str | None:
    """最近一条用户消息的文本。"""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return getattr(message, "content", "")

    return None


def format_history(messages: list) -> str:
    """把消息转成摘要器可读的对话文本。"""
    lines = []

    for message in messages:
        role = "用户" if isinstance(message, HumanMessage) else "助手"
        content = getattr(message, "content", "").strip()

        if content:
            lines.append(f"{role}: {content}")

    return "\n".join(lines)


def build_tool_message(
    ok: bool,
    name: str,
    call_id: str,
    content: str,
    retryable: bool = False,
) -> ToolMessage:
    """构造工具结果消息；非 JSON 文本按原字符串返回。"""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        payload = content

    return ToolMessage(
        content=json.dumps(
            {
                "ok": ok,
                "retryable": retryable,
                "content": payload,
            },
            ensure_ascii=False,
        ),
        name=name,
        tool_call_id=call_id,
        additional_kwargs={"retryable": retryable},
    )

from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class TodoItem(BaseModel):
    task_id: str = Field(description="任务唯一 ID")
    content: str = Field(description="任务描述、目标")
    agent: Literal[
        "geo_agent",
        "poi_agent",
        "route_agent",
    ] = Field(description="交给哪个子 Agent")
    depends_on: list[str] = Field(
        default_factory=list,
        description="前置任务 ID",
    )
    status: Literal[
        "pending",
        "in_progress",
        "completed",
        "failed",
    ] = Field(default="pending", description="任务状态")

    @field_validator("task_id", mode="before")
    @classmethod
    def _stringify_task_id(cls, value):
        return str(value) if value is not None else value

    @field_validator("depends_on", mode="before")
    @classmethod
    def _stringify_depends_on(cls, value):
        if value is None:
            return []

        if isinstance(value, (str, int)):
            return [str(value)]

        return [str(item) for item in value]


class MissingInfo(BaseModel):
    field: str = Field(description="缺失信息字段")
    question: str = Field(description="向用户提问")
    reason: str = Field(description="原因")

class TaskPlan(BaseModel):
    goal: str = Field(description="任务目标")
    tasks: list[TodoItem] = Field(
        default_factory=list,
        description="多任务详细信息"
    )
    missing: list[MissingInfo] = Field(
        default_factory=list,
        description="缺失的必要查询信息，向用户提问"
    )


class RouteDecision(BaseModel):
    next_node: Literal["answer", "planning"] = Field(
        description="answer 直接回答，planning 进入任务规划",
    )
    need_memory: bool = Field(default=False, description="是否需要检索更早的对话记忆")

class DispatchDecision(BaseModel):
    task_id: str | None = Field(
        default=None,
        description="本次派发任务的唯一 ID；没有可执行任务时为空",
    )
    reason: str = Field(default="", description="派发理由")

    @field_validator("task_id", mode="before")
    @classmethod
    def _stringify_task_id(cls, value):
        return str(value) if value is not None else value


class TaskResult(BaseModel):
    task_id: str = Field(description="任务唯一 ID")
    status: Literal["success", "failed"] = Field(description="是否成功完成")
    summary: str = Field(description="任务完成结果摘要")
    data: dict = Field(
        default_factory=dict,
        description="结构化执行结果",
    )
    error: str | None = Field(
        default=None,
        description="失败原因",
    )

    @field_validator("task_id", mode="before")
    @classmethod
    def _stringify_task_id(cls, value):
        return str(value) if value is not None else value


class MemoryCard(BaseModel):
    topic: str = Field(description="本轮主题")
    summary: str = Field(description="本轮摘要", max_length=100)
    keywords: list[str] = Field(description="用于检索的关键词", max_length=5)

    def is_empty(self) -> bool:
        return (
            not self.topic.strip()
            and not self.summary.strip()
            and not self.keywords
        )

    def to_context(self) -> str:
        """拼接文本"""
        return (
            f"主题: {self.topic}\n"
            f"摘要: {self.summary}\n"
            f"关键词: {'、'.join(self.keywords)}"
        )

class UserPreferences(BaseModel):
    city: str = Field(default="", description="常驻城市")
    language: str = Field(default="zh", description="偏好语言")
    transport: list[str] = Field(default_factory=list, description="出行方式偏好")
    food: list[str] = Field(default_factory=list, description="口味偏好")
    interests: list[str] = Field(default_factory=list, description="游玩兴趣偏好")
    custom: dict[str, str] = Field(default_factory=dict, description="其他偏好")

    def merge(self, patch: "UserPreferences") -> "UserPreferences":
        """去重、追加更新偏好"""
        current = self.model_dump()
        incoming = patch.model_dump(exclude_unset=True)

        list_fields = {"transport", "food", "interests"}
        dict_fields = {"custom"}
        scalar_fields = {"city", "language"}

        for field in list_fields:
            if field not in incoming:
                continue

            old = current.get(field) or []
            new = incoming.get(field) or []

            current[field] = list(dict.fromkeys([*old, *new]))

        for field in dict_fields:
            if field not in incoming:
                continue

            current[field] = {**(current.get(field) or {}), **(incoming.get(field) or {})}

        for field in scalar_fields:
            value = incoming.get(field)

            if value:
                current[field] = value

        try:
            return UserPreferences.model_validate(current)
        except ValidationError:
            raise

import json

from langchain_core.messages import SystemMessage
from agents.prompts.loader import load_prompt
from common.model import get_llm
from common.pydantic_model import UserPreferences
from common.call_with_retry import call_with_retry
from services.db.user_pofile_repository import UserProfileRepository


async def extract_profile(buffer: list[dict], old_profile: dict) -> UserPreferences:
    """抽取用户偏好"""
    messages = [
        SystemMessage(content=(
            load_prompt("extract_profile")
            + "\n\n五轮对话:\n"
            + json.dumps(buffer, ensure_ascii=False)
            + "\n\n旧偏好:\n"
            + json.dumps(old_profile, ensure_ascii=False)
        ))
    ]
    llm_with_schema = get_llm().with_structured_output(
        schema=UserPreferences,
        method="json_mode"
    )
    try:
        response: UserPreferences = await call_with_retry(lambda: llm_with_schema.ainvoke(messages))
    except Exception as e:
        print(f"用户偏好抽取失败: {e}")
        raise

    return response


async def update_profile(buffer: list[dict], repo: UserProfileRepository, config) -> dict | None:
    """更新用户偏好"""
    old_profile = UserPreferences.model_validate(config['configurable']['user_profile'])

    try:
        new_profile: UserPreferences = old_profile.merge(
            await extract_profile(buffer, old_profile.model_dump())
        )
    except Exception:
        return None

    if new_profile.model_dump() == old_profile.model_dump():
        return None

    await repo.upsert(
        config['configurable']['user_id'],
        new_profile.model_dump(),
    )

    return new_profile.model_dump()
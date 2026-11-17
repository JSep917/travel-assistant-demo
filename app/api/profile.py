from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_user_id
from common.pydantic_model import UserPreferences

router = APIRouter(prefix="/api", tags=["profile"])


class ProfileUpdate(BaseModel):
    profile: dict = Field(default_factory=dict)


def get_repo(request: Request):
    return request.app.state.profile_repo


@router.get("/profile")
async def get_profile(
    request: Request,
    user_id: str = Depends(get_user_id),
):
    data = await get_repo(request).get(user_id) or {}
    return {"profile": UserPreferences.model_validate(data).model_dump()}


@router.put("/profile")
async def update_profile(
    body: ProfileUpdate,
    request: Request,
    user_id: str = Depends(get_user_id),
):
    try:
        profile = UserPreferences.model_validate(body.profile)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    await get_repo(request).upsert(
        user_id,
        profile.model_dump(),
    )

    return {"profile": profile.model_dump()}

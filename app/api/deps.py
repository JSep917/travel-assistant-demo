from fastapi import Header, Request

from services.chat.chat_service import ChatService


def get_user_id(
    x_user_id: str | None = Header(
        default=None,
        alias="X-User-Id",
    ),
) -> str:
    return x_user_id or "test-user"


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service

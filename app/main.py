import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from services.db.checkpoint_manager import CheckpointManager
from services.db.thread_repository import ThreadRepository
from services.db.user_pofile_repository import UserProfileRepository
from services.chat.chat_service import ChatService
from services.redis.tool_cache import close_tool_cache
from agents.context.store import close_memory_store
from fastapi.responses import RedirectResponse
from agents.main_graph.graph import build_main_graph
from app.api.chat import router as chat_router
from app.api.profile import router as profile_router
from app.api.threads import router as threads_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """生命周期"""
    pool = AsyncConnectionPool(
        conninfo=settings.POSTGRE_URI,
        min_size=1,
        max_size=10,
        open=False,
    )
    try:
        await pool.open()

        schema_file = (
            Path(__file__).resolve().parent.parent
            / "services"
            / "db"
            / "001.sql"
        )
        statements = [
            statement.strip()
            for statement in schema_file.read_text(encoding="utf-8").split(";")
            if statement.strip()
        ]

        async with pool.connection() as conn:
            for statement in statements:
                await conn.execute(statement)

        async with AsyncPostgresSaver.from_conn_string(
            settings.POSTGRE_URI
        ) as checkpointer:
            manager = CheckpointManager(checkpointer)
            await manager.setup()

            thread_repo = ThreadRepository(pool)
            profile_repo = UserProfileRepository(pool)
            graph = await build_main_graph(checkpointer)

            app.state.thread_repo = thread_repo
            app.state.profile_repo = profile_repo
            app.state.graph = graph
            app.state.checkpoint_manager = manager
            app.state.chat_service = ChatService(
                thread_repo=thread_repo,
                profile_repo=profile_repo,
                graph=graph,
            )

            yield

            try:
                await asyncio.wait_for(
                    app.state.chat_service.wait_background(),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                pass

    finally:
        await close_memory_store()
        await pool.close()
        await close_tool_cache()

app = FastAPI(title="智能出行助手", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat_router)
app.include_router(threads_router)
app.include_router(profile_router)

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount(
        "/app",
        StaticFiles(directory=FRONTEND_DIST, html=True),
        name="frontend",
    )


@app.get("/")
async def root():
    if FRONTEND_DIST.exists():
        return RedirectResponse(url="/app/")

    return RedirectResponse(url="/docs")
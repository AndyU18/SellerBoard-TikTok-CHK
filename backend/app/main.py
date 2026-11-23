import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analytics_api import router as analytics_router
from app.api import router
from app.config import settings
from app.db import SessionLocal, init_db
from app.sync_jobs import auto_sync_loop
from app.users_api import router as users_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    task = asyncio.create_task(auto_sync_loop(SessionLocal)) if settings.auto_sync_enabled else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="CHK Profit", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(analytics_router)
app.include_router(users_router)

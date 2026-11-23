import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analytics_api import router as analytics_router
from app.api import router
from app.assistant_api import router as assistant_router
from app.config import settings
from app.db import SessionLocal, init_db
from app.export_api import router as export_router
from app.notifications_api import router as notifications_router
from app.notify import digest_loop
from app.redo_api import router as redo_router
from app.sku_api import router as sku_router
from app.sync_jobs import auto_sync_loop
from app.users_admin_api import router as users_admin_router
from app.users_api import router as users_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    # Tareas de fondo: sincronización automática y resumen por correo/Slack (este último no hace nada
    # mientras CHK_DIGEST_ENABLED sea falso o no haya canales configurados).
    tasks = []
    if settings.auto_sync_enabled:
        tasks.append(asyncio.create_task(auto_sync_loop(SessionLocal)))
    if settings.digest_enabled:
        tasks.append(asyncio.create_task(digest_loop(SessionLocal)))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
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
app.include_router(redo_router)
app.include_router(assistant_router)
app.include_router(notifications_router)
app.include_router(sku_router)
app.include_router(export_router)
app.include_router(users_admin_router)
app.include_router(users_router)

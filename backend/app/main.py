import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.action_api import router as action_router
from app.ads_api import router as ads_router
from app.analytics_api import router as analytics_router
from app.api import router
from app.assistant_api import router as assistant_router
from app.chartdata_dinero import router as chartdata_dinero_router
from app.chartdata_operacion import router as chartdata_operacion_router
from app.chartdata_pedidos import router as chartdata_pedidos_router
from app.chartdata_precios import router as chartdata_precios_router
from app.chartdata_productos import router as chartdata_productos_router
from app.chartdata_resumen import router as chartdata_resumen_router
from app.config import settings
from app.cost_risk_api import router as cost_risk_router
from app.creator_profit_api import router as creator_profit_router
from app.db import SessionLocal, init_db
from app.export_api import router as export_router
from app.min_price_api import router as min_price_router
from app.notifications_api import router as notifications_router
from app.notify import digest_loop
from app.order_profit_api import router as order_profit_router
from app.redo_api import router as redo_router
from app.sample_policy_api import router as sample_policy_router
from app.settlement_aging_api import router as settlement_aging_router
from app.settlement_api import router as settlement_router
from app.simulator_api import router as simulator_router
from app.sku_api import router as sku_router
from app.sync_jobs import auto_sync_loop
from app.users_admin_api import router as users_admin_router
from app.users_api import router as users_router
from app.walmart_api import router as walmart_router
from app.walmart_payments_api import router as walmart_payments_router


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
app.include_router(ads_router)
app.include_router(chartdata_dinero_router)
app.include_router(action_router)
app.include_router(walmart_router)
app.include_router(walmart_payments_router)
app.include_router(cost_risk_router)
app.include_router(settlement_aging_router)  # antes que settlement_router: "/aging" no debe caer en "/{id}/waterfall"
app.include_router(sample_policy_router)
app.include_router(min_price_router)
app.include_router(order_profit_router)
app.include_router(settlement_router)
app.include_router(creator_profit_router)
app.include_router(simulator_router)
app.include_router(chartdata_pedidos_router)
app.include_router(chartdata_productos_router)
app.include_router(chartdata_resumen_router)
app.include_router(chartdata_precios_router)
app.include_router(chartdata_operacion_router)
app.include_router(assistant_router)
app.include_router(notifications_router)
app.include_router(sku_router)
app.include_router(export_router)
app.include_router(users_admin_router)
app.include_router(users_router)

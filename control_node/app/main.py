"""Aplicación principal del ControlNode.

Al arrancar, espera a que PostgreSQL esté disponible y luego registra los
DataNodes declarados en la configuración, de modo que el servicio no acepte
peticiones hasta tener su estado inicial listo. Después lanza el monitor de
DataNodes, que corre en segundo plano mientras el servicio esté activo.
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.datanodes import router as datanodes_router
from app.api.files import router as files_router
from app.api.filesystem import router as filesystem_router
from app.api.groups import router as groups_router
from app.config import get_settings
from app.db.session import SessionLocal, wait_for_database
from app.services.bootstrap_service import BootstrapService
from app.services.datanode_health_client import DataNodeHealthClient
from app.services.datanode_service import DataNodeRegistryService
from app.services.node_monitor_service import NodeMonitorService

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Espera activa a la base de datos antes de servir peticiones.
    wait_for_database()

    settings = get_settings()
    session = SessionLocal()
    try:
        # Garantiza el usuario de sistema y el directorio raíz (idempotente).
        BootstrapService(session).run()
        # Registra los DataNodes configurados (operación idempotente).
        DataNodeRegistryService(session).sync_from_config(settings.datanodes)
    finally:
        session.close()

    # Arranca el monitor de DataNodes como tarea de fondo.
    monitor = NodeMonitorService(
        session_factory=SessionLocal,
        health_client=DataNodeHealthClient(),
        interval_seconds=settings.health_check_interval_seconds,
        failure_threshold=settings.datanode_failure_threshold,
    )
    monitor_task = asyncio.create_task(monitor.run_forever())

    yield

    # Al apagar el ControlNode se detiene el monitor de forma ordenada.
    monitor_task.cancel()
    with suppress(asyncio.CancelledError):
        await monitor_task


# Instancia principal de la aplicación del ControlNode.
app = FastAPI(title="DFSha ControlNode", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(datanodes_router)
app.include_router(groups_router)
app.include_router(filesystem_router)
app.include_router(files_router)


@app.get("/health")
def health():
    # Endpoint de verificación de disponibilidad del servicio.
    return {"servicio": "control_node", "estado": "ok"}

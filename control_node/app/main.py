"""Aplicación principal del ControlNode.

Al arrancar, espera a que PostgreSQL esté disponible y luego registra los
DataNodes declarados en la configuración, de modo que el servicio no acepte
peticiones hasta tener su estado inicial listo.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.datanodes import router as datanodes_router
from app.api.groups import router as groups_router
from app.config import get_settings
from app.db.session import SessionLocal, wait_for_database
from app.services.datanode_service import DataNodeRegistryService

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Espera activa a la base de datos antes de servir peticiones.
    wait_for_database()

    # Registra los DataNodes configurados (operación idempotente).
    settings = get_settings()
    session = SessionLocal()
    try:
        DataNodeRegistryService(session).sync_from_config(settings.datanodes)
    finally:
        session.close()

    yield


# Instancia principal de la aplicación del ControlNode.
app = FastAPI(title="DFSha ControlNode", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(datanodes_router)
app.include_router(groups_router)


@app.get("/health")
def health():
    # Endpoint de verificación de disponibilidad del servicio.
    return {"servicio": "control_node", "estado": "ok"}

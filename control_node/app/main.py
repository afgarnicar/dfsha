"""Aplicación principal del ControlNode.

Al arrancar, espera a que PostgreSQL esté disponible antes de aceptar
peticiones, de modo que el servicio no responda hasta tener conexión a la base
de datos de metadatos.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db.session import wait_for_database

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Espera activa a la base de datos antes de servir peticiones.
    wait_for_database()
    yield


# Instancia principal de la aplicación del ControlNode.
app = FastAPI(title="DFSha ControlNode", lifespan=lifespan)


@app.get("/health")
def health():
    # Endpoint de verificación de disponibilidad del servicio.
    return {"servicio": "control_node", "estado": "ok"}

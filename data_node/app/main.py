"""Aplicación principal del DataNode.

El DataNode es deliberadamente simple: guarda, lee, borra y replica bloques, y
reporta su salud. Aquí solo se crea la aplicación y se registran los routers.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.blocks import router as blocks_router
from app.api.health import router as health_router
from app.config import get_settings
from app.dependencies import get_block_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Se carga la configuración al arrancar para fallar rápido si falta algo.
get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prepara la carpeta de bloques y limpia temporales de escrituras interrumpidas.
    store = get_block_store()
    store.ensure_root()
    removed = store.remove_leftover_temp_files()
    if removed:
        logger.info("Se borraron %d archivos temporales huérfanos.", removed)
    yield


# Instancia principal de la aplicación del DataNode.
app = FastAPI(title="DFSha DataNode", lifespan=lifespan)

app.include_router(health_router)
app.include_router(blocks_router)

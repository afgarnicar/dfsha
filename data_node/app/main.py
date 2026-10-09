"""Aplicación principal del DataNode.

El DataNode es deliberadamente simple: guarda, lee y borra bloques, y reporta
su salud. Aquí solo se crea la aplicación y se registran los routers.
"""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings

# Se carga la configuración al arrancar para fallar rápido si falta algo.
get_settings()

# Instancia principal de la aplicación del DataNode.
app = FastAPI(title="DFSha DataNode")

app.include_router(health_router)

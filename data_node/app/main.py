import os
from fastapi import FastAPI

# Instancia principal de la aplicación del DataNode
app = FastAPI(title="DFSha DataNode")

# Identificador único del nodo, configurado por variable de entorno
NODE_ID = os.getenv("NODE_ID", "DN-desconocido")


@app.get("/health")
def health():
    # Endpoint de verificación de disponibilidad del nodo
    return {
        "node_id": NODE_ID,
        "estado": "ok",
        "espacio_libre_bytes": _get_free_space(),
    }


def _get_free_space() -> int:
    # Retorna el espacio libre en bytes del directorio de almacenamiento
    storage_root = os.getenv("STORAGE_ROOT", "/data/dfsha")
    try:
        stat = os.statvfs(storage_root)
        return stat.f_bavail * stat.f_frsize
    except FileNotFoundError:
        return 0
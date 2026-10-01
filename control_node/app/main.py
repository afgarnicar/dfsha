from fastapi import FastAPI

# Instancia principal de la aplicación del ControlNode
app = FastAPI(title="DFSha ControlNode")


@app.get("/health")
def health():
    # Endpoint de verificación de disponibilidad del servicio
    return {"servicio": "control_node", "estado": "ok"}
"""Utilidades comunes de las pruebas de punta a punta de DFSha.

Las pruebas hablan con el sistema real por HTTP, igual que el cliente:
- el ControlNode en ``DFSHA_TEST_CN`` (por defecto http://127.0.0.1:18000);
- los DataNodes por su API interna (HTTPS + clave interna) solo para contar o
  revisar bloques.

Para apagar o prender DataNodes, ejecutar código dentro de un contenedor o
consultar PostgreSQL se usa un "backend". Por defecto es Docker Compose sobre
``tests/docker-compose.yml``. Se puede cambiar con
``DFSHA_TEST_BACKEND=modulo:Clase`` (por ejemplo, para correr las pruebas
contra procesos locales sin Docker).
"""

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

CN = os.environ.get("DFSHA_TEST_CN", "http://127.0.0.1:18000")
DN_PORTS = [int(p) for p in os.environ.get("DFSHA_TEST_DN_PORTS", "18001,18002,18003").split(",")]
API_KEY = os.environ.get("DFSHA_TEST_API_KEY", "clave-interna-de-pruebas")
PASSWORD = "clave12345"
MB = 1024 * 1024  # el clúster de pruebas usa bloques de 1 MB

# Sin verificación de certificado: el de los DataNodes es interno y autofirmado.
_http = httpx.Client(timeout=300, verify=False)


# --------------------------------------------------------------------- resultados

class Results:
    """Cuenta las verificaciones que pasan y las que fallan."""

    def __init__(self) -> None:
        self.ok = 0
        self.fail = 0

    def check(self, name: str, cond: bool, extra: object = "") -> bool:
        if cond:
            self.ok += 1
            print(f"[PASA]  {name}", flush=True)
        else:
            self.fail += 1
            print(f"[FALLA] {name}  {extra}", flush=True)
        return bool(cond)

    def section(self, title: str) -> None:
        print(f"\n=== {title} ===", flush=True)

    def finish(self) -> None:
        print(f"\n=== Resultado: {self.ok} pasaron, {self.fail} fallaron ===", flush=True)
        sys.exit(0 if self.fail == 0 else 1)


# ------------------------------------------------------------------- backend

class DockerBackend:
    """Controla el clúster de pruebas con Docker Compose."""

    def __init__(self) -> None:
        self.compose = ["docker", "compose", "-f", str(HERE / "docker-compose.yml")]

    def _run(self, *args: str, env: dict | None = None, check: bool = True) -> str:
        full_env = dict(os.environ, **(env or {}))
        out = subprocess.run(
            [*self.compose, *args], capture_output=True, text=True, env=full_env,
            encoding="utf-8", errors="replace",
        )
        if check and out.returncode != 0:
            raise RuntimeError(f"docker compose {' '.join(args)} falló:\n{out.stdout}\n{out.stderr}")
        return out.stdout

    def build(self) -> None:
        self._run("build")

    def reset(self, config: dict) -> None:
        """Borra el clúster (con sus datos) y lo levanta de cero con ``config``."""
        env = {f"DFSHA_TEST_{k}": str(v) for k, v in config.items()}
        self._run("down", "-v", "--remove-orphans", env=env)
        # Primero la base y los DataNodes, para que el ControlNode los vea en
        # línea desde su primer chequeo.
        self._run("up", "-d", "postgres", "data_node_1", "data_node_2", "data_node_3", env=env)
        for i in (1, 2, 3):
            wait_dn_up(i)
        self._run("up", "-d", "control_node", env=env)

    def down(self) -> None:
        self._run("down", "-v", "--remove-orphans", check=False)

    def stop_dn(self, i: int) -> None:
        self._run("stop", f"data_node_{i}")

    def start_dn(self, i: int) -> None:
        self._run("start", f"data_node_{i}")

    def dn_py(self, i: int, code: str) -> str:
        """Corre Python dentro del DataNode i. ``DFSHA_DATA`` = su carpeta de bloques."""
        return self._run("exec", "-T", "-e", "DFSHA_DATA=/data/dfsha", f"data_node_{i}", "python", "-c", code)

    def cn_py(self, code: str) -> str:
        """Corre Python dentro del ControlNode. ``DFSHA_TEMP`` = su carpeta temporal."""
        return self._run("exec", "-T", "-e", "DFSHA_TEMP=/tmp/dfsha", "control_node", "python", "-c", code)

    def sql(self, query: str) -> str:
        return self._run("exec", "-T", "postgres", "psql", "-U", "dfsha", "-d", "dfsha", "-tAc", query).strip()


def _load_backend():
    spec = os.environ.get("DFSHA_TEST_BACKEND")
    if not spec:
        return DockerBackend()
    module, _, cls = spec.partition(":")
    return getattr(importlib.import_module(module), cls)()


backend = _load_backend()


# ----------------------------------------------------------------- DataNodes

def dn_url(i: int, path: str) -> str:
    return f"https://127.0.0.1:{DN_PORTS[i - 1]}{path}"


def dn_healthy(i: int) -> bool:
    try:
        return _http.get(dn_url(i, "/health"), timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def wait_dn_up(i: int, limit: float = 60) -> bool:
    end = time.time() + limit
    while time.time() < end:
        if dn_healthy(i):
            return True
        time.sleep(0.5)
    return False


def stop_dn(i: int) -> None:
    backend.stop_dn(i)
    end = time.time() + 30
    while time.time() < end and dn_healthy(i):
        time.sleep(0.3)


def start_dn(i: int) -> None:
    backend.start_dn(i)
    wait_dn_up(i)


def dn_block_ids(i: int) -> set[str]:
    """Ids de los bloques que el DataNode i tiene en su disco (por su API)."""
    r = _http.get(dn_url(i, "/blocks"), headers={"X-DFSHA-API-Key": API_KEY}, timeout=30)
    r.raise_for_status()
    return {b["block_id"] for b in r.json()["blocks"]}


def dn_total() -> int:
    """Cantidad total de copias guardadas en los 3 DataNodes."""
    return sum(len(dn_block_ids(i)) for i in (1, 2, 3))


def datanode_status() -> dict[str, str]:
    return {n["name"]: n["status"] for n in _http.get(f"{CN}/datanodes").json()}


def wait_status(name: str, wanted: str, limit: float = 40) -> bool:
    end = time.time() + limit
    while time.time() < end:
        if datanode_status().get(name) == wanted:
            return True
        time.sleep(0.5)
    return False


def wait_cluster_ready(limit: float = 120) -> bool:
    """Espera a que el ControlNode responda y vea los 3 DataNodes ONLINE."""
    end = time.time() + limit
    while time.time() < end:
        try:
            if _http.get(f"{CN}/health", timeout=3).status_code == 200:
                if set(datanode_status().values()) == {"ONLINE"} and len(datanode_status()) == 3:
                    return True
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    return False


# ------------------------------------------------------------- API del cliente

def user(name: str) -> dict:
    """Registra (si hace falta) e inicia sesión. Devuelve la cabecera con el JWT."""
    cred = {"username": name, "password": PASSWORD}
    _http.post(f"{CN}/auth/register", json=cred)
    token = _http.post(f"{CN}/auth/login", json=cred).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def unique(prefix: str) -> str:
    return f"{prefix}{int(time.time() * 1000) % 10_000_000}"


def put(h, directory, name, data, overwrite=None):
    form = {"directory_path": directory}
    if overwrite is not None:
        form["overwrite"] = "true" if overwrite else "false"
    return _http.post(f"{CN}/files", headers=h, data=form, files={"file": (name, data)})


def get(h, path):
    return _http.get(f"{CN}/files/download", headers=h, params={"path": path})


def rm(h, path):
    return _http.request("DELETE", f"{CN}/files", headers=h, json={"path": path})


def ls(h, directory) -> list[str]:
    return [f["name"] for f in _http.get(f"{CN}/filesystem/list", headers=h, params={"path": directory}).json()["files"]]


def op(h, path, mode):
    return _http.post(f"{CN}/files/open", headers=h, json={"path": path, "mode": mode})


def rd(h, handle, offset, length):
    return _http.post(f"{CN}/files/read", headers=h, json={"handle": handle, "offset": offset, "length": length})


def wr(h, handle, offset, data):
    return _http.post(f"{CN}/files/write", headers=h, data={"handle": handle, "offset": str(offset)},
                      files={"file": ("data", data)})


def cl(h, handle):
    return _http.post(f"{CN}/files/close", headers=h, json={"handle": handle})


def api(h, method, path, **kwargs):
    return _http.request(method, f"{CN}{path}", headers=h, **kwargs)


def raw():
    """Cliente HTTP sin cabeceras, para casos especiales (sin token, /health)."""
    return _http


def as_json(text: str):
    return json.loads(text)

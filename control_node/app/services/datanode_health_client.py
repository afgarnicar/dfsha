"""Cliente HTTP que consulta el endpoint /health de los DataNodes.

Solo se encarga de la parte de red: preguntarle a cada nodo cómo está y
traducir la respuesta a un resultado simple. No toca la base de datos.
"""

import asyncio
from dataclasses import dataclass

import httpx

# Tiempo máximo de espera por cada verificación. Si un nodo tarda más que esto,
# la verificación cuenta como fallida.
HEALTH_CHECK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class HealthCheckTarget:
    """Datos mínimos de un DataNode para poder consultarlo."""

    datanode_id: int
    name: str
    host: str
    port: int

    @property
    def health_url(self) -> str:
        """URL del endpoint de salud del nodo."""
        return f"http://{self.host}:{self.port}/health"


@dataclass(frozen=True)
class HealthCheckResult:
    """Resultado de verificar un DataNode."""

    datanode_id: int
    ok: bool
    # Espacio libre reportado por el nodo; solo tiene valor si ok es True.
    free_space_bytes: int | None = None
    # Motivo del fallo, para dejarlo en los logs; solo tiene valor si ok es False.
    error: str | None = None


class DataNodeHealthClient:
    """Consulta en paralelo la salud de varios DataNodes."""

    def __init__(self, timeout_seconds: float = HEALTH_CHECK_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds

    async def check_many(
        self, targets: list[HealthCheckTarget]
    ) -> list[HealthCheckResult]:
        """Verifica todos los nodos al mismo tiempo y devuelve un resultado por nodo."""
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await asyncio.gather(
                *(self._check_one(client, target) for target in targets)
            )

    async def _check_one(
        self, client: httpx.AsyncClient, target: HealthCheckTarget
    ) -> HealthCheckResult:
        """Verifica un solo nodo. Cualquier error cuenta como verificación fallida."""
        try:
            response = await client.get(target.health_url)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return HealthCheckResult(
                datanode_id=target.datanode_id,
                ok=False,
                error=f"sin respuesta válida ({type(error).__name__}: {error})",
            )

        # Se valida que conteste el nodo esperado, para detectar un host mal configurado.
        if body.get("node_id") != target.name:
            return HealthCheckResult(
                datanode_id=target.datanode_id,
                ok=False,
                error=(
                    f"respondió el nodo {body.get('node_id')!r} "
                    f"en lugar de {target.name!r}"
                ),
            )
        if body.get("status") != "ok":
            return HealthCheckResult(
                datanode_id=target.datanode_id,
                ok=False,
                error=f"el nodo reportó estado {body.get('status')!r}",
            )

        free_space = body.get("free_space_bytes")
        if not isinstance(free_space, int) or free_space < 0:
            return HealthCheckResult(
                datanode_id=target.datanode_id,
                ok=False,
                error=f"espacio libre inválido: {free_space!r}",
            )

        return HealthCheckResult(
            datanode_id=target.datanode_id, ok=True, free_space_bytes=free_space
        )

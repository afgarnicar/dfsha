"""Descarga paralela de bloques desde los DataNodes.

Para cada bloque intenta primero la réplica PRIMARY y, si falla o su checksum
no coincide, la BACKUP. Los bytes se escriben directo a un archivo temporal
mientras se calcula el SHA-256, así que un bloque nunca se carga completo en
memoria durante la descarga.

Las descargas corren en paralelo con ``httpx.AsyncClient`` y ``asyncio``, con
un límite de descargas simultáneas (MAX_PARALLEL_DOWNLOADS) para no abrir
cientos de conexiones a la vez.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Tiempos de espera: corto para conectar, amplio para transferir un bloque.
_CONNECT_TIMEOUT_SECONDS = 5.0
_TRANSFER_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class ReplicaSource:
    """Un DataNode que tiene una copia del bloque."""

    node_name: str
    host: str
    port: int
    role: str


@dataclass(frozen=True)
class BlockFetchPlan:
    """Qué bloque descargar, a dónde escribirlo y de qué réplicas, en orden."""

    block_index: int
    storage_key: str
    checksum: str
    target_path: Path
    replicas: list[ReplicaSource]


@dataclass(frozen=True)
class BlockFetchResult:
    """Bloque descargado y validado, con la réplica que lo entregó."""

    block_index: int
    node_name: str
    role: str
    size_bytes: int


@dataclass
class BlockUnavailableError(Exception):
    """Ninguna réplica del bloque entregó contenido válido."""

    block_index: int
    attempts: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        detail = "; ".join(self.attempts) or "no hay réplicas registradas"
        return f"bloque {self.block_index + 1}: {detail}"


class BlockFetcher:
    """Descarga y valida bloques en paralelo con respaldo PRIMARY -> BACKUP."""

    def __init__(self, max_parallel: int) -> None:
        self._max_parallel = max(1, max_parallel)

    def fetch_all(self, plans: list[BlockFetchPlan]) -> list[BlockFetchResult]:
        """Descarga todos los bloques. Si uno no se puede obtener, falla todo.

        Se ejecuta desde código síncrono: crea su propio ciclo de asyncio.
        """
        return asyncio.run(self._fetch_all(plans))

    async def _fetch_all(self, plans: list[BlockFetchPlan]) -> list[BlockFetchResult]:
        semaphore = asyncio.Semaphore(self._max_parallel)
        timeout = httpx.Timeout(_TRANSFER_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
        limits = httpx.Limits(max_connections=self._max_parallel)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            tasks = [
                asyncio.create_task(self._fetch_with_limit(client, semaphore, plan))
                for plan in plans
            ]
            try:
                return await asyncio.gather(*tasks)
            except BaseException:
                # Si un bloque falla, no tiene sentido seguir con los demás.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    async def _fetch_with_limit(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        plan: BlockFetchPlan,
    ) -> BlockFetchResult:
        async with semaphore:
            return await self._fetch_one(client, plan)

    async def _fetch_one(
        self, client: httpx.AsyncClient, plan: BlockFetchPlan
    ) -> BlockFetchResult:
        """Prueba las réplicas en orden hasta que una entregue el bloque válido."""
        error = BlockUnavailableError(block_index=plan.block_index)
        for replica in plan.replicas:
            outcome = await self._download_from(client, plan, replica)
            if isinstance(outcome, int):
                if replica.role != "PRIMARY":
                    logger.warning(
                        "Bloque %d de %s recuperado desde la réplica %s (%s).",
                        plan.block_index + 1, plan.storage_key, replica.role, replica.node_name,
                    )
                return BlockFetchResult(
                    block_index=plan.block_index,
                    node_name=replica.node_name,
                    role=replica.role,
                    size_bytes=outcome,
                )
            logger.warning(
                "Falló la réplica %s (%s) del bloque %d: %s",
                replica.role, replica.node_name, plan.block_index + 1, outcome,
            )
            error.attempts.append(f"{replica.role} en {replica.node_name}: {outcome}")
        raise error

    async def _download_from(
        self, client: httpx.AsyncClient, plan: BlockFetchPlan, replica: ReplicaSource
    ) -> int | str:
        """Descarga el bloque de una réplica al archivo temporal.

        Devuelve el tamaño en bytes si salió bien y el checksum coincide, o un
        texto con el motivo del fallo. Si falla, borra lo que alcanzó a escribir.
        """
        url = f"http://{replica.host}:{replica.port}/blocks/{plan.storage_key}"
        digest = hashlib.sha256()
        size = 0
        try:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return f"HTTP {response.status_code}"
                with plan.target_path.open("wb") as target:
                    async for chunk in response.aiter_bytes():
                        digest.update(chunk)
                        target.write(chunk)
                        size += len(chunk)
        except httpx.HTTPError as http_error:
            plan.target_path.unlink(missing_ok=True)
            return f"sin respuesta ({type(http_error).__name__})"

        if digest.hexdigest() != plan.checksum:
            plan.target_path.unlink(missing_ok=True)
            return "checksum no coincide"
        return size

"""Cliente para enviar bloques a los DataNodes.

Envía el contenido de un bloque (ya cifrado) a un DataNode mediante su endpoint
``POST /blocks``, usando ``multipart/form-data``. Reintenta ante fallos
temporales hasta un máximo configurable, porque una réplica puede fallar por un
problema puntual de red.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

# Tiempos de espera: amplio para la transferencia, porque un bloque puede pesar
# hasta 64 MB; corto para la conexión inicial.
_CONNECT_TIMEOUT_SECONDS = 5.0
_TRANSFER_TIMEOUT_SECONDS = 120.0


class BlockSendError(Exception):
    """No fue posible almacenar el bloque en el DataNode tras los reintentos."""


class BlockClient:
    """Envía bloques a los DataNodes con reintentos."""

    def __init__(self, max_retries: int = 3) -> None:
        self._max_retries = max_retries

    def send_block(
        self,
        host: str,
        port: int,
        block_id: str,
        checksum: str,
        content: bytes,
    ) -> None:
        """Envía un bloque a un DataNode, reintentando ante fallos temporales.

        Lanza ``BlockSendError`` si tras todos los intentos no se pudo almacenar.
        """
        url = f"http://{host}:{port}/blocks"
        timeout = httpx.Timeout(
            _TRANSFER_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS
        )
        last_error: str = ""

        for attempt in range(1, self._max_retries + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        url,
                        data={"block_id": block_id, "checksum": checksum},
                        files={"file": (block_id, content, "application/octet-stream")},
                    )
                if response.status_code == 201:
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            except httpx.HTTPError as error:
                last_error = f"{type(error).__name__}: {error}"

            logger.warning(
                "Fallo al enviar el bloque %s a %s:%d (intento %d/%d): %s",
                block_id,
                host,
                port,
                attempt,
                self._max_retries,
                last_error,
            )

        raise BlockSendError(
            f"No se pudo almacenar el bloque {block_id} en {host}:{port} "
            f"tras {self._max_retries} intentos. Último error: {last_error}"
        )

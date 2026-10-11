"""Cliente HTTP del CLI hacia el ControlNode.

Centraliza las llamadas a la API: arma la URL base, agrega el token de
autenticación cuando existe y traduce los errores en mensajes claros. El resto
de los comandos usan este cliente en lugar de hablar con httpx directamente.

La verificación del certificado TLS está desactivada por defecto porque el
sistema usa un certificado autofirmado en el entorno académico. La comunicación
sigue cifrada; solo no se valida la identidad del servidor contra una autoridad
certificadora.
"""

from typing import Any

import httpx

# Tiempos de espera: amplio para subir/bajar archivos grandes.
_CONNECT_TIMEOUT_SECONDS = 10.0
_TRANSFER_TIMEOUT_SECONDS = 600.0


class ApiError(Exception):
    """Error devuelto por el ControlNode o fallo de comunicación."""


class ApiClient:
    """Cliente de la API del ControlNode para el CLI."""

    def __init__(self, base_url: str, token: str | None = None, verify_tls: bool = False) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._verify = verify_tls

    def _headers(self) -> dict[str, str]:
        """Encabezados comunes: incluye el token si hay sesión iniciada."""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _client(self) -> httpx.Client:
        timeout = httpx.Timeout(_TRANSFER_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
        return httpx.Client(base_url=self._base_url, verify=self._verify, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: dict | None = None,
        files: dict | None = None,
        params: dict | None = None,
        stream_to: str | None = None,
    ) -> Any:
        """Hace una petición y devuelve el JSON de la respuesta.

        Con ``stream_to`` guarda el cuerpo binario en esa ruta (para descargas) y
        no intenta interpretarlo como JSON.
        """
        try:
            with self._client() as client:
                if stream_to is not None:
                    return self._download(client, method, path, params, stream_to)
                response = client.request(
                    method,
                    path,
                    json=json,
                    data=data,
                    files=files,
                    params=params,
                    headers=self._headers(),
                )
        except httpx.HTTPError as error:
            raise ApiError(
                f"No fue posible contactar al ControlNode en {self._base_url}: "
                f"{type(error).__name__}."
            ) from error

        return self._handle(response)

    def _download(self, client, method, path, params, stream_to) -> None:
        """Descarga una respuesta binaria a un archivo."""
        with client.stream(method, path, params=params, headers=self._headers()) as response:
            if response.status_code >= 400:
                response.read()
                self._handle(response)
            with open(stream_to, "wb") as target:
                for chunk in response.iter_bytes():
                    target.write(chunk)

    def _handle(self, response: httpx.Response) -> Any:
        """Devuelve el JSON si la respuesta fue exitosa, o lanza ApiError."""
        if response.status_code >= 400:
            detail = self._error_detail(response)
            raise ApiError(detail)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extrae el mensaje de error del cuerpo, con un respaldo legible."""
        try:
            body = response.json()
            if isinstance(body, dict) and "detail" in body:
                return str(body["detail"])
        except ValueError:
            pass
        return f"Error del servidor (HTTP {response.status_code})."

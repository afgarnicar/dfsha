"""Comandos de archivos: subir (put), descargar (get) y eliminar (rm)."""

import os
from pathlib import Path

import typer

from client.api_client import ApiClient, ApiError
from client.session import load_session

app = typer.Typer(help="Transferencia de archivos.")


def _client() -> ApiClient:
    session = load_session()
    return ApiClient(session.base_url, token=session.token)


def _fail(error: ApiError) -> None:
    typer.secho(f"Error: {error}", fg=typer.colors.RED)
    raise typer.Exit(code=1)


@app.command()
def put(
    local_path: str = typer.Argument(..., help="Archivo local a subir."),
    directory_path: str = typer.Argument(..., help="Directorio destino en el DFS."),
) -> None:
    """Sube un archivo local al directorio indicado del sistema distribuido."""
    source = Path(local_path)
    if not source.is_file():
        typer.secho(f"Error: el archivo '{local_path}' no existe.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        with source.open("rb") as handle:
            result = _client().request(
                "POST",
                "/files",
                data={"directory_path": directory_path},
                files={"file": (source.name, handle, "application/octet-stream")},
            )
    except ApiError as error:
        _fail(error)
    typer.secho(
        f"Subido: {source.name} -> {directory_path} "
        f"({result['size_bytes']} bytes, {result['block_count']} bloque(s)).",
        fg=typer.colors.GREEN,
    )


@app.command()
def get(
    remote_path: str = typer.Argument(..., help="Ruta del archivo en el DFS."),
    output: str = typer.Option(None, "--output", "-o", help="Ruta local de destino."),
) -> None:
    """Descarga un archivo del sistema distribuido."""
    destination = output or os.path.basename(remote_path)
    try:
        _client().request(
            "GET", "/files/download", params={"path": remote_path}, stream_to=destination
        )
    except ApiError as error:
        _fail(error)
    typer.secho(f"Descargado: {remote_path} -> {destination}.", fg=typer.colors.GREEN)


@app.command()
def rm(remote_path: str = typer.Argument(..., help="Ruta del archivo a eliminar.")) -> None:
    """Elimina un archivo del sistema distribuido."""
    try:
        _client().request("DELETE", "/files", json={"path": remote_path})
    except ApiError as error:
        _fail(error)
    typer.secho(f"Eliminado: {remote_path}.", fg=typer.colors.GREEN)

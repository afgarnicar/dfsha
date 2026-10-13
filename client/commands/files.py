"""Comandos de archivos: subir (put), descargar (get), eliminar (rm) y acceso
por partes (open, read, write, close)."""

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


# --- Acceso por partes: open, read, write, close ---------------------------


@app.command("open")
def open_file(
    remote_path: str = typer.Argument(..., help="Ruta del archivo en el DFS."),
    mode: str = typer.Option("read", "--mode", "-m", help="Modo de apertura: read o write."),
) -> None:
    """Abre un archivo y muestra su handle (en modo write toma el lock de escritura)."""
    mode = mode.strip().upper()
    if mode not in ("READ", "WRITE"):
        typer.secho("Error: el modo debe ser read o write.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        result = _client().request("POST", "/files/open", json={"path": remote_path, "mode": mode})
    except ApiError as error:
        _fail(error)
    typer.secho(
        f"Abierto: {result['name']} en modo {result['mode']} ({result['size_bytes']} bytes).",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"Handle: {result['handle']}")
    typer.echo(f"Vence: {result['expires_at']}")


@app.command()
def read(
    handle: str = typer.Argument(..., help="Handle devuelto por open."),
    offset: int = typer.Argument(..., min=0, help="Byte desde el que se lee."),
    length: int = typer.Argument(..., min=0, help="Cantidad de bytes a leer."),
    output: str = typer.Option(
        "-", "--output", "-o", help="Archivo local de destino (por defecto, la pantalla)."
    ),
) -> None:
    """Lee un rango del archivo abierto."""
    try:
        _client().request(
            "POST",
            "/files/read",
            json={"handle": handle, "offset": offset, "length": length},
            stream_to=output,
        )
    except ApiError as error:
        _fail(error)
    if output != "-":
        typer.secho(f"Leídos los bytes en {output}.", fg=typer.colors.GREEN)


@app.command()
def write(
    handle: str = typer.Argument(..., help="Handle devuelto por open en modo write."),
    offset: int = typer.Argument(..., min=0, help="Byte desde el que se escribe."),
    text: str = typer.Option(None, "--text", "-t", help="Texto a escribir."),
    from_file: str = typer.Option(None, "--from", "-f", help="Archivo local con los datos a escribir."),
) -> None:
    """Escribe datos en el archivo abierto, desde el offset indicado."""
    if (text is None) == (from_file is None):
        typer.secho("Error: indique --text o --from (solo uno de los dos).", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if from_file is not None and not Path(from_file).is_file():
        typer.secho(f"Error: el archivo '{from_file}' no existe.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        if text is not None:
            content = ("data", text.encode("utf-8"), "application/octet-stream")
            result = _client().request(
                "POST",
                "/files/write",
                data={"handle": handle, "offset": str(offset)},
                files={"file": content},
            )
        else:
            with open(from_file, "rb") as source:
                result = _client().request(
                    "POST",
                    "/files/write",
                    data={"handle": handle, "offset": str(offset)},
                    files={"file": ("data", source, "application/octet-stream")},
                )
    except ApiError as error:
        _fail(error)
    typer.secho(
        f"Escrito. Tamaño nuevo: {result['size_bytes']} bytes (versión {result['version']}).",
        fg=typer.colors.GREEN,
    )


@app.command()
def close(handle: str = typer.Argument(..., help="Handle a cerrar.")) -> None:
    """Cierra el handle (y libera el lock si era de escritura)."""
    try:
        _client().request("POST", "/files/close", json={"handle": handle})
    except ApiError as error:
        _fail(error)
    typer.secho("Handle cerrado.", fg=typer.colors.GREEN)

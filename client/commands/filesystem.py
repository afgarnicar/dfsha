"""Comandos del sistema de archivos lógico: mkdir, ls, cd, rmdir."""

import typer

from client.api_client import ApiClient, ApiError
from client.session import load_session

app = typer.Typer(help="Gestión de directorios.")


def _client() -> ApiClient:
    session = load_session()
    return ApiClient(session.base_url, token=session.token)


def _fail(error: ApiError) -> None:
    typer.secho(f"Error: {error}", fg=typer.colors.RED)
    raise typer.Exit(code=1)


@app.command()
def mkdir(path: str = typer.Argument(..., help="Ruta del directorio a crear.")) -> None:
    """Crea un directorio en la ruta indicada."""
    try:
        directory = _client().request("POST", "/directories", json={"path": path})
    except ApiError as error:
        _fail(error)
    typer.secho(f"Directorio creado: {path} (id {directory['id']}).", fg=typer.colors.GREEN)


@app.command()
def ls(path: str = typer.Argument("/", help="Ruta del directorio a listar.")) -> None:
    """Lista el contenido de un directorio."""
    try:
        listing = _client().request("GET", "/filesystem/list", params={"path": path})
    except ApiError as error:
        _fail(error)

    directories = listing["subdirectories"]
    files = listing["files"]
    if not directories and not files:
        typer.echo("(vacío)")
        return
    for d in directories:
        typer.secho(f"  {d['name']}/", fg=typer.colors.BLUE)
    for f in files:
        typer.echo(f"  {f['name']}  ({f['size_bytes']} bytes, {f['status']})")


@app.command()
def cd(path: str = typer.Argument(..., help="Ruta del directorio a validar.")) -> None:
    """Comprueba que se puede entrar a un directorio."""
    try:
        directory = _client().request("GET", "/filesystem/resolve", params={"path": path})
    except ApiError as error:
        _fail(error)
    typer.secho(f"Directorio válido: {path} (id {directory['id']}).", fg=typer.colors.GREEN)


@app.command()
def rmdir(path: str = typer.Argument(..., help="Ruta del directorio vacío a eliminar.")) -> None:
    """Elimina un directorio vacío."""
    try:
        _client().request("DELETE", "/directories", json={"path": path})
    except ApiError as error:
        _fail(error)
    typer.secho(f"Directorio eliminado: {path}.", fg=typer.colors.GREEN)

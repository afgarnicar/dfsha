"""Comandos de permisos: info, chmod, chgrp, ACL y grupos."""

import typer

from client.api_client import ApiClient, ApiError
from client.session import load_session

app = typer.Typer(help="Permisos de archivos y directorios: chmod, grupo, ACL y grupos.")


def _client() -> ApiClient:
    session = load_session()
    return ApiClient(session.base_url, token=session.token)


def _fail(error: ApiError) -> None:
    typer.secho(f"Error: {error}", fg=typer.colors.RED)
    raise typer.Exit(code=1)


def _show(info: dict) -> None:
    """Muestra los permisos de un recurso, parecido a un ls -l."""
    group = info["group"] or "-"
    typer.echo(f"{info['path']}  ({info['type']})")
    typer.echo(f"  permisos: {info['mode_string']} ({info['mode']})")
    typer.echo(f"  dueño:    {info['owner']}")
    typer.echo(f"  grupo:    {group}")
    if info["acl"]:
        typer.echo("  ACL:")
        for entry in info["acl"]:
            flags = (
                ("r" if entry["read"] else "-")
                + ("w" if entry["write"] else "-")
                + ("x" if entry["execute"] else "-")
            )
            typer.echo(f"    {entry['subject_type']}:{entry['subject']}  {flags}")


def _parse_flags(flags: str) -> dict:
    """Convierte un texto como 'rw-' o 'rx' en read/write/execute."""
    flags = flags.lower()
    if any(char not in "rwx-" for char in flags):
        typer.secho("Error: los permisos deben usar solo r, w, x o -.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return {"read": "r" in flags, "write": "w" in flags, "execute": "x" in flags}


def _parse_subject(subject: str) -> tuple[str, str]:
    """Convierte 'user:juan' o 'group:equipo' en (tipo, nombre)."""
    kind, _, name = subject.partition(":")
    if kind not in ("user", "group") or not name:
        typer.secho(
            "Error: el sujeto debe ser user:<usuario> o group:<grupo>.", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)
    return kind, name


@app.command()
def info(path: str = typer.Argument(..., help="Ruta del archivo o directorio.")) -> None:
    """Muestra dueño, grupo, permisos y ACL de un archivo o directorio."""
    try:
        _show(_client().request("GET", "/access", params={"path": path}))
    except ApiError as error:
        _fail(error)


@app.command()
def chmod(
    mode: str = typer.Argument(..., help="Permisos en octal, por ejemplo 750."),
    path: str = typer.Argument(..., help="Ruta del archivo o directorio."),
) -> None:
    """Cambia los permisos rwx de dueño, grupo y otros."""
    try:
        _show(_client().request("PATCH", "/access/mode", json={"path": path, "mode": mode}))
    except ApiError as error:
        _fail(error)


@app.command()
def chgrp(
    group: str = typer.Argument(..., help="Grupo a asignar, o '-' para quitarlo."),
    path: str = typer.Argument(..., help="Ruta del archivo o directorio."),
) -> None:
    """Asocia un archivo o directorio a un grupo."""
    body = {"path": path, "group": None if group == "-" else group}
    try:
        _show(_client().request("PATCH", "/access/group", json=body))
    except ApiError as error:
        _fail(error)


@app.command("acl-set")
def acl_set(
    subject: str = typer.Argument(..., help="user:<usuario> o group:<grupo>."),
    flags: str = typer.Argument(..., help="Permisos, por ejemplo r--, rw- o rwx."),
    path: str = typer.Argument(..., help="Ruta del archivo o directorio."),
) -> None:
    """Da permisos específicos a un usuario o grupo sobre un recurso."""
    kind, name = _parse_subject(subject)
    body = {"path": path, "subject_type": kind, "subject": name, **_parse_flags(flags)}
    try:
        _show(_client().request("PUT", "/access/acl", json=body))
    except ApiError as error:
        _fail(error)


@app.command("acl-rm")
def acl_rm(
    subject: str = typer.Argument(..., help="user:<usuario> o group:<grupo>."),
    path: str = typer.Argument(..., help="Ruta del archivo o directorio."),
) -> None:
    """Quita los permisos específicos de un usuario o grupo."""
    kind, name = _parse_subject(subject)
    body = {"path": path, "subject_type": kind, "subject": name}
    try:
        _show(_client().request("DELETE", "/access/acl", json=body))
    except ApiError as error:
        _fail(error)


@app.command("group-create")
def group_create(name: str = typer.Argument(..., help="Nombre del grupo.")) -> None:
    """Crea un grupo. Quien lo crea queda como miembro."""
    try:
        _client().request("POST", "/access/groups", json={"name": name})
    except ApiError as error:
        _fail(error)
    typer.secho(f"Grupo '{name}' creado.", fg=typer.colors.GREEN)


@app.command("group-add")
def group_add(
    group: str = typer.Argument(..., help="Nombre del grupo."),
    username: str = typer.Argument(..., help="Usuario a agregar."),
) -> None:
    """Agrega un usuario a un grupo del que usted es miembro."""
    try:
        _client().request("POST", f"/access/groups/{group}/members", json={"username": username})
    except ApiError as error:
        _fail(error)
    typer.secho(f"'{username}' agregado al grupo '{group}'.", fg=typer.colors.GREEN)

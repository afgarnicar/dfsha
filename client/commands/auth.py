"""Comandos de autenticación del CLI: registro, inicio y cierre de sesión."""

import typer

from client.api_client import ApiClient, ApiError
from client.session import clear_session, load_session, save_session

app = typer.Typer(help="Autenticación de usuarios.")


@app.command()
def register(
    username: str = typer.Argument(..., help="Nombre de usuario a crear."),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="Contraseña."),
) -> None:
    """Registra un usuario nuevo en el sistema."""
    session = load_session()
    client = ApiClient(session.base_url)
    try:
        user = client.request(
            "POST", "/auth/register", json={"username": username, "password": password}
        )
    except ApiError as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(f"Usuario '{user['username']}' registrado (id {user['id']}).", fg=typer.colors.GREEN)


@app.command()
def login(
    username: str = typer.Argument(..., help="Nombre de usuario."),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="Contraseña."),
) -> None:
    """Inicia sesión y guarda el token para los siguientes comandos."""
    session = load_session()
    client = ApiClient(session.base_url)
    try:
        result = client.request(
            "POST", "/auth/login", json={"username": username, "password": password}
        )
    except ApiError as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    session.token = result["access_token"]
    save_session(session)
    typer.secho(f"Sesión iniciada como '{username}'.", fg=typer.colors.GREEN)


@app.command()
def logout() -> None:
    """Cierra la sesión local (borra el token guardado)."""
    clear_session()
    typer.secho("Sesión cerrada.", fg=typer.colors.GREEN)


@app.command()
def me() -> None:
    """Muestra el usuario autenticado actualmente."""
    session = load_session()
    client = ApiClient(session.base_url, token=session.token)
    try:
        user = client.request("GET", "/auth/me")
    except ApiError as error:
        typer.secho(f"Error: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.echo(f"id={user['id']}  usuario={user['username']}  activo={user['is_active']}")

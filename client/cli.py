"""Punto de entrada del cliente de línea de comandos de DFSha.

Agrupa los comandos de autenticación, directorios y archivos, y permite
configurar la URL del ControlNode. Para subir y bajar archivos, primero hay que
iniciar sesión con ``login``.

Ejemplos de uso:

    python -m client.cli set-url https://mi-controlnode
    python -m client.cli auth login andres
    python -m client.cli fs mkdir /home/andres/documentos
    python -m client.cli files put ./informe.pdf /home/andres/documentos
    python -m client.cli files get /home/andres/documentos/informe.pdf
    python -m client.cli files open /home/andres/documentos/notas.txt --mode write
    python -m client.cli files write <handle> 0 --text "Hola"
    python -m client.cli files read <handle> 0 4
    python -m client.cli files close <handle>
"""

import typer

from client.commands import auth, files, filesystem
from client.session import load_session, save_session

app = typer.Typer(help="Cliente de línea de comandos del sistema de archivos distribuido DFSha.")

# Sub-comandos agrupados por área.
app.add_typer(auth.app, name="auth")
app.add_typer(filesystem.app, name="fs")
app.add_typer(files.app, name="files")


@app.command("set-url")
def set_url(base_url: str = typer.Argument(..., help="URL del ControlNode, por ejemplo https://10.0.0.10")) -> None:
    """Configura la URL del ControlNode usada por los comandos."""
    session = load_session()
    session.base_url = base_url
    save_session(session)
    typer.secho(f"URL del ControlNode configurada en {base_url}.", fg=typer.colors.GREEN)


@app.command("show-url")
def show_url() -> None:
    """Muestra la URL del ControlNode configurada actualmente."""
    typer.echo(load_session().base_url)


if __name__ == "__main__":
    app()

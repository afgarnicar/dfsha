"""Suite 6 — Redundancia: copias que se restauran solas, copias huérfanas y el CLI.

Tiempos de esta suite: chequeo de DataNodes cada 1 s (OFFLINE tras 3 fallos),
limpieza cada 2 s y margen de 5 s para borrar una copia huérfana.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from helpers import (MB, ROOT, Results, backend, dn_block_ids, get, put, start_dn, stop_dn, unique, user,
                     wait_status, CN, PASSWORD)

CONFIG = {"FAILURE_THRESHOLD": 3, "ORPHAN_GRACE": 5, "CLEANUP_INTERVAL": 2}

UNDER_REPLICATED = (
    "select count(*) from blocks b join files f on f.id=b.file_id where f.status='AVAILABLE' "
    "and b.version=f.current_version and (select count(*) from block_locations l join datanodes d "
    "on d.id=l.datanode_id where l.block_id=b.id and d.status='ONLINE') < 2"
)
FRESH = "f" * 32  # id de una copia "recién llegada" que ningún archivo registra


def meta_keys(name: str) -> set[str]:
    return set(backend.sql(
        "select b.storage_key from blocks b join block_locations l on l.block_id=b.id "
        f"join datanodes d on d.id=l.datanode_id where d.name='{name}'").split())


def under_replicated() -> int:
    return int(backend.sql(UNDER_REPLICATED))


def main() -> None:
    t = Results()
    name = unique("redun")
    h = user(name)
    home = f"/home/{name}"
    data = os.urandom(6 * MB)  # 6 bloques
    put(h, home, "d.bin", data)
    en_dn2 = dn_block_ids(2)

    t.section("Bloques que quedan con una sola copia se restauran solos")
    stop_dn(2)
    stop_dn(3)  # caen casi juntos: al caer DN2 no hay a dónde copiar sus bloques
    t.check("DN2 y DN3 pasan a OFFLINE", wait_status("DN2", "OFFLINE") and wait_status("DN3", "OFFLINE"))
    time.sleep(3)
    t.check("hay bloques con menos de 2 copias vivas (no había a dónde copiar)", under_replicated() > 0,
            under_replicated())
    start_dn(3)
    t.check("DN3 vuelve a ONLINE", wait_status("DN3", "ONLINE"))
    time.sleep(6)  # la revisión periódica corre cada 2 s
    t.check("la revisión periódica restauró las copias: 0 bloques con una sola copia", under_replicated() == 0,
            under_replicated())
    r = get(h, f"{home}/d.bin")
    t.check("el archivo se descarga idéntico usando solo DN1 y DN3", r.status_code == 200 and r.content == data)

    t.section("Copias huérfanas en un DataNode que vuelve")
    huerfanas = en_dn2 - meta_keys("DN2")
    t.check("DN2 tenía copias que los metadatos ya no le asignan (se recrearon en otro nodo)", len(huerfanas) > 0,
            len(huerfanas))
    start_dn(2)
    t.check("DN2 vuelve a ONLINE", wait_status("DN2", "ONLINE"))
    backend.dn_py(2, f"import os;open(os.path.join(os.environ['DFSHA_DATA'],'{FRESH}'),'wb').write(b'x')")
    time.sleep(3.5)  # más de una pasada de limpieza, pero menos que el margen de 5 s
    en_disco = dn_block_ids(2)
    t.check("la limpieza borró las copias huérfanas viejas", not (huerfanas & en_disco), len(huerfanas & en_disco))
    t.check("una copia reciente (menos de 5 s) no se toca", FRESH in en_disco)
    t.check("todo lo demás que queda en DN2 está registrado", en_disco - meta_keys("DN2") <= {FRESH})
    time.sleep(5)
    t.check("cuando la copia sin registrar pasa el margen, también se borra", FRESH not in dn_block_ids(2))
    for i in (1, 3):
        t.check(f"DN{i} no perdió ninguna copia registrada", meta_keys(f"DN{i}") <= dn_block_ids(i))
    r = get(h, f"{home}/d.bin")
    t.check("el archivo se sigue descargando idéntico", r.status_code == 200 and r.content == data)

    t.section("Cliente CLI: put, open, write, read, close, get")
    with tempfile.TemporaryDirectory() as tmp:
        # La sesión del CLI se guarda en una carpeta temporal, no en la del usuario.
        env = dict(os.environ, HOME=tmp, USERPROFILE=tmp, PYTHONIOENCODING="utf-8")

        def cli(*args):
            # stdin vacío y tiempo límite: si algo pidiera datos por teclado, falla en vez de quedarse esperando.
            out = subprocess.run([sys.executable, "-m", "client.cli", *args], cwd=str(ROOT), env=env,
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120,
                                 encoding="utf-8", errors="replace")
            return out.returncode, out.stdout + out.stderr

        nota = Path(tmp) / "nota.txt"
        nota.write_text("hola mundo distribuido", encoding="utf-8")
        cli("set-url", CN)
        t.check("login por CLI", cli("auth", "login", name, "--password", PASSWORD)[0] == 0)
        t.check("put por CLI", cli("files", "put", str(nota), home)[0] == 0)
        code, out = cli("files", "open", f"{home}/nota.txt", "--mode", "write")
        handle = next((line.split()[-1] for line in out.splitlines() if line.startswith("Handle:")), "")
        t.check("open en modo write por CLI", code == 0 and bool(handle), out)
        code, out = cli("files", "write", handle, "5", "--text", "MUNDO")
        t.check("write por CLI (versión 2)", code == 0 and "versión 2" in out, out)
        code, out = cli("files", "read", handle, "0", "100")
        t.check("read por CLI muestra el cambio", out.strip() == "hola MUNDO distribuido", repr(out))
        code, out = cli("files", "open", f"{home}/nota.txt")
        t.check("open en modo read mientras hay lock -> error claro", code != 0 and "bloqueado" in out, out)
        t.check("close por CLI", cli("files", "close", handle)[0] == 0)
        code, out = cli("files", "read", handle, "0", "4")
        t.check("read después de close -> error claro", code != 0 and "handle" in out.lower(), out)
        copia = Path(tmp) / "copia.txt"
        cli("files", "get", f"{home}/nota.txt", "-o", str(copia))
        t.check("get por CLI trae el archivo modificado",
                copia.exists() and copia.read_text(encoding="utf-8") == "hola MUNDO distribuido")
        code, out = cli("files", "put", str(nota), home, "--overwrite")
        t.check("put --overwrite por CLI reemplaza el archivo", code == 0 and "Reemplazado" in out, out)
    t.finish()


if __name__ == "__main__":
    main()

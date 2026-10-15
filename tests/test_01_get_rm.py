"""Suite 1 — GET (descarga) y rm (borrado), con DataNodes caídos y copias dañadas.

Umbral de fallos alto: al apagar un DataNode queda SUSPECT pero no pasa a
OFFLINE, así sus bloques no se re-replican y el GET tiene que usar la otra copia.
"""

import concurrent.futures as cf
import os
import time

from helpers import (MB, Results, backend, datanode_status, dn_block_ids, dn_total, get, ls,
                     put, raw, rm, start_dn, stop_dn, unique, user, wait_status, CN)

CONFIG = {"FAILURE_THRESHOLD": 60}

# Código que se corre dentro del DataNode 1 para dañar (y luego reparar) sus copias.
CORRUPT = (
    "import os,shutil;d=os.environ['DFSHA_DATA'];b='/tmp/respaldo';os.makedirs(b,exist_ok=True)\n"
    "for n in os.listdir(d):\n"
    " p=os.path.join(d,n)\n"
    " if os.path.isfile(p) and not n.startswith('.'):\n"
    "  shutil.copy(p,os.path.join(b,n));data=open(p,'rb').read();open(p,'wb').write(b'basura'+data[6:])"
)
RESTORE = (
    "import os,shutil;d=os.environ['DFSHA_DATA'];b='/tmp/respaldo'\n"
    "for n in os.listdir(b): shutil.copy(os.path.join(b,n),os.path.join(d,n))\n"
    "shutil.rmtree(b)"
)
TEMP_COUNT = "import os;d=os.environ['DFSHA_TEMP'];print(len(os.listdir(d)) if os.path.isdir(d) else 0)"


def temp_empty() -> bool:
    return backend.cn_py(TEMP_COUNT).strip() == "0"


def revive(i: int) -> None:
    start_dn(i)
    wait_status(f"DN{i}", "ONLINE")


def main() -> None:
    t = Results()
    beto, juan = unique("beto"), unique("juan")
    hb, hj = user(beto), user(juan)
    home = f"/home/{beto}"

    t.section("GET básico")
    data = os.urandom(2_500_000)  # 3 bloques de 1 MB
    t.check("PUT de 2,5 MB (3 bloques)", put(hb, home, "datos.bin", data).status_code == 201)
    r = get(hb, f"{home}/datos.bin")
    t.check("GET responde 200", r.status_code == 200, r.text[:200])
    t.check("GET devuelve el archivo idéntico byte a byte", r.content == data)
    t.check("Content-Length correcto", r.headers.get("content-length") == str(len(data)))
    t.check("Los temporales del ControlNode se borran después del GET", temp_empty())
    t.check("PUT de un archivo vacío", put(hb, home, "vacio.txt", b"").status_code == 201)
    r = get(hb, f"{home}/vacio.txt")
    t.check("GET de un archivo vacío", r.status_code == 200 and r.content == b"", r.text[:200])
    uno = b"x" * MB
    put(hb, home, "justo1mb.bin", uno)
    t.check("GET de un archivo de exactamente 1 bloque", get(hb, f"{home}/justo1mb.bin").content == uno)

    t.section("Errores del GET")
    t.check("GET de un archivo que no existe -> 404", get(hb, f"{home}/nada.bin").status_code == 404)
    t.check("GET de un archivo de otro usuario -> 403", get(hj, f"{home}/datos.bin").status_code == 403)
    t.check("GET sin token -> 401",
            raw().get(f"{CN}/files/download", params={"path": f"{home}/datos.bin"}).status_code == 401)

    t.section("Tolerancia a fallos del GET")
    stop_dn(1)
    r = get(hb, f"{home}/datos.bin")
    t.check("Con DN1 apagado, el GET usa la otra copia y sale idéntico",
            r.status_code == 200 and r.content == data, r.text[:200])
    revive(1)
    backend.dn_py(1, CORRUPT)
    r = get(hb, f"{home}/datos.bin")
    t.check("Con las copias de DN1 dañadas, el GET detecta el checksum malo y usa la otra copia",
            r.status_code == 200 and r.content == data, r.text[:200])
    backend.dn_py(1, RESTORE)
    stop_dn(1)
    stop_dn(2)
    r = get(hb, f"{home}/datos.bin")
    t.check("Con las 2 copias de un bloque caídas -> 503", r.status_code == 503, f"{r.status_code} {r.text[:200]}")
    t.check("Los temporales se borran también cuando el GET falla", temp_empty())
    revive(1)
    revive(2)

    t.section("GET concurrentes")
    with cf.ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: get(hb, f"{home}/datos.bin"), range(6)))
    t.check("6 GET al mismo tiempo, todos idénticos",
            all(r.status_code == 200 and r.content == data for r in results))
    t.check("Los temporales se borran después de los GET concurrentes", temp_empty())

    t.section("rm")
    antes = dn_total()
    t.check("rm de un archivo de otro usuario -> 403", rm(hj, f"{home}/datos.bin").status_code == 403)
    r = rm(hb, f"{home}/datos.bin")
    t.check("rm con todos los nodos arriba -> deleted",
            r.status_code == 200 and r.json()["status"] == "deleted", r.text)
    t.check("El archivo ya no sale en ls", "datos.bin" not in ls(hb, home))
    t.check("GET del archivo borrado -> 404", get(hb, f"{home}/datos.bin").status_code == 404)
    t.check("Sus 6 copias (3 bloques x 2) se borraron de los DataNodes", dn_total() == antes - 6,
            f"{antes} -> {dn_total()}")
    t.check("rm otra vez -> 404", rm(hb, f"{home}/datos.bin").status_code == 404)
    t.check("Se puede volver a subir un archivo con el mismo nombre",
            put(hb, home, "datos.bin", data).status_code == 201)

    t.section("rm con un DataNode caído (limpieza pendiente)")
    put(hb, home, "otro.bin", os.urandom(2_200_000))  # 3 bloques
    antes = {i: len(dn_block_ids(i)) for i in (1, 2, 3)}
    stop_dn(3)
    r = rm(hb, f"{home}/otro.bin")
    body = r.json()
    pend = body.get("pending_replicas", 0)
    t.check("rm con DN3 apagado -> pending_cleanup",
            body.get("status") == "pending_cleanup" and pend > 0, r.text)
    t.check("El archivo pendiente no sale en ls", "otro.bin" not in ls(hb, home))
    t.check("GET del archivo pendiente -> 404", get(hb, f"{home}/otro.bin").status_code == 404)
    vivos = len(dn_block_ids(1)) + len(dn_block_ids(2))
    t.check("Las copias de los nodos vivos sí se borraron",
            vivos == antes[1] + antes[2] - (6 - pend), f"{antes} -> {vivos}, pendientes {pend}")
    data3 = os.urandom(1_500_000)  # 2 bloques
    r = put(hb, home, "otro.bin", data3)
    t.check("Con el borrado aún pendiente, se puede subir otro archivo con el mismo nombre",
            r.status_code == 201, r.text)
    revive(3)
    time.sleep(6)  # la limpieza corre cada 2 s
    total_antes = sum(antes.values())
    t.check("Al volver DN3, la limpieza borra las copias que faltaban",
            dn_total() == total_antes - 6 + 4, f"{total_antes} -> {dn_total()}")
    r = get(hb, f"{home}/otro.bin")
    t.check("La limpieza no tocó el archivo nuevo con el mismo nombre",
            r.status_code == 200 and r.content == data3, r.text[:200])

    t.section("Una subida fallida (FAILED) libera su nombre")
    backend.sql("update files set status='FAILED' where name='otro.bin'")
    time.sleep(6)
    t.check("Después de limpiar un FAILED, se puede subir otra vez con el mismo nombre",
            put(hb, home, "otro.bin", data3).status_code == 201)
    t.check("Los 3 DataNodes terminan ONLINE", set(datanode_status().values()) == {"ONLINE"})
    t.finish()


if __name__ == "__main__":
    main()

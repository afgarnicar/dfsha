"""Suite 2 — open / read / write / close y el WRITE LOCK.

El lock vence a los 5 s en el clúster de pruebas (LOCK_TIMEOUT_SECONDS=5).
"""

import concurrent.futures as cf
import os
import time

from helpers import (MB, Results, cl, dn_total, get, op, put, rd, rm, start_dn, stop_dn, unique, user,
                     wait_status, wr)

CONFIG = {"LOCK_TIMEOUT": 5}


def main() -> None:
    t = Results()
    beto, juan = unique("beto"), unique("juan")
    hb, hj = user(beto), user(juan)
    path = f"/home/{beto}/doc.bin"
    data = bytearray(os.urandom(int(2.5 * MB)))  # 3 bloques
    put(hb, f"/home/{beto}", "doc.bin", bytes(data))

    t.section("open / read / close")
    r = op(hb, path, "READ")
    t.check("open en modo READ -> 200", r.status_code == 200, r.text)
    h = r.json()["handle"]
    t.check("open devuelve el tamaño del archivo", r.json()["size_bytes"] == len(data))
    r = rd(hb, h, 0, 100)
    t.check("read de los primeros 100 bytes", r.status_code == 200 and r.content == data[:100], r.text[:200])
    t.check("read que cruza dos bloques", rd(hb, h, MB - 10, 20).content == data[MB - 10:MB + 10])
    t.check("read en la mitad del bloque 2",
            rd(hb, h, int(1.5 * MB), 1000).content == data[int(1.5 * MB):int(1.5 * MB) + 1000])
    t.check("read que pasa del final se recorta al final", rd(hb, h, len(data) - 5, 100).content == data[-5:])
    t.check("read con offset después del final -> 400", rd(hb, h, len(data) + 1, 10).status_code == 400)
    t.check("read del archivo completo", rd(hb, h, 0, len(data)).content == bytes(data))
    t.check("close -> 204", cl(hb, h).status_code == 204)
    t.check("read después de close -> 410", rd(hb, h, 0, 10).status_code == 410)
    t.check("close otra vez -> 410", cl(hb, h).status_code == 410)
    t.check("open de otro usuario sin permiso -> 403", op(hj, path, "READ").status_code == 403)
    t.check("open de un archivo que no existe -> 404", op(hb, f"/home/{beto}/nada", "READ").status_code == 404)
    hr = op(hb, path, "READ").json()["handle"]
    t.check("un usuario no puede usar el handle de otro -> 410", rd(hj, hr, 0, 10).status_code == 410)

    t.section("WRITE LOCK")
    r = op(hb, path, "WRITE")
    t.check("open en modo WRITE toma el lock -> 200", r.status_code == 200, r.text)
    hw = r.json()["handle"]
    t.check("otro open WRITE mientras hay lock -> 423", op(hb, path, "WRITE").status_code == 423)
    t.check("open READ mientras hay lock -> 423", op(hb, path, "READ").status_code == 423)
    t.check("read con un handle READ abierto antes del lock -> 423", rd(hb, hr, 0, 10).status_code == 423)
    t.check("GET mientras hay lock -> 423", get(hb, path).status_code == 423)
    t.check("rm mientras hay lock -> 423", rm(hb, path).status_code == 423)
    t.check("el dueño del lock sí puede leer con su handle", rd(hb, hw, 0, 50).content == data[:50])

    t.section("write (reemplazo seguro por versión nueva)")
    patch = os.urandom(30)
    r = wr(hb, hw, MB - 15, patch)  # cruza el borde entre el bloque 1 y el 2
    t.check("write en la mitad del archivo -> 200", r.status_code == 200, r.text)
    data[MB - 15:MB + 15] = patch
    t.check("el tamaño no cambia", r.json()["size_bytes"] == len(data))
    t.check("la versión sube a 2", r.json()["version"] == 2)
    t.check("read con el handle ve el cambio", rd(hb, hw, MB - 20, 40).content == bytes(data[MB - 20:MB + 20]))
    extra = os.urandom(MB)
    r = wr(hb, hw, len(data), extra)
    data.extend(extra)
    t.check("write justo al final (EOF) hace crecer el archivo",
            r.status_code == 200 and r.json()["size_bytes"] == len(data), r.text)
    t.check("write con offset después del final -> 400", wr(hb, hw, len(data) + 1, b"x").status_code == 400)
    t.check("close del handle WRITE libera el lock", cl(hb, hw).status_code == 204)
    r = get(hb, path)
    t.check("GET después del close trae el archivo modificado", r.status_code == 200 and r.content == bytes(data))
    hr2 = op(hb, path, "READ").json()["handle"]
    t.check("write con un handle READ -> 400", wr(hb, hr2, 0, b"x").status_code == 400)
    cl(hb, hr2)
    time.sleep(4)  # la limpieza periódica corre cada 2 s
    t.check("las versiones viejas se borraron de los DataNodes (4 bloques x 2 copias)", dn_total() == 8,
            f"hay {dn_total()} copias")

    t.section("El lock vence solo si el cliente desaparece")
    hw1 = op(hb, path, "WRITE").json()["handle"]
    time.sleep(6)
    r = op(hb, path, "WRITE")
    t.check("pasado el vencimiento, otro open WRITE sí toma el lock", r.status_code == 200, r.text)
    t.check("el handle viejo ya no sirve para escribir -> 410", wr(hb, hw1, 0, b"x").status_code == 410)
    cl(hb, r.json().get("handle"))

    t.section("Varios open WRITE al mismo tiempo")
    def intentar(_):
        try:
            return op(hb, path, "WRITE")
        except Exception as error:  # noqa: BLE001 — una conexión cortada también es una falla
            return error

    rondas = []
    for _ in range(10):
        with cf.ThreadPoolExecutor(3) as pool:
            resp = list(pool.map(intentar, range(3)))
        rondas.append(sorted(getattr(r, "status_code", 0) for r in resp))
        for r in resp:
            if getattr(r, "status_code", 0) == 200:
                cl(hb, r.json()["handle"])
        time.sleep(0.2)
    t.check("en 10 rondas de 3 open WRITE simultáneos, siempre gana uno solo (200, 423, 423)",
            all(c == [200, 423, 423] for c in rondas), rondas)

    t.section("Un write que no puede cumplir el factor 2 no cambia nada")
    stop_dn(2)
    stop_dn(3)
    wait_status("DN2", "OFFLINE")
    wait_status("DN3", "OFFLINE")
    hw3 = op(hb, path, "WRITE").json()["handle"]
    r = wr(hb, hw3, 0, os.urandom(int(3.5 * MB)))
    t.check("write con un solo DataNode vivo -> 503", r.status_code == 503, f"{r.status_code} {r.text[:200]}")
    cl(hb, hw3)
    for i in (2, 3):
        start_dn(i)
        wait_status(f"DN{i}", "ONLINE")
    r = get(hb, path)
    t.check("la versión anterior sigue intacta", r.status_code == 200 and r.content == bytes(data))
    t.finish()


if __name__ == "__main__":
    main()

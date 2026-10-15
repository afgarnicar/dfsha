"""Suite 4 — PUT con DataNodes que fallan: réplica en otro nodo y fallas sin rastro.

El monitor revisa cada 300 s en esta suite: al apagar un DataNode, el
ControlNode todavía lo cree ONLINE, intenta guardarle bloques, falla y tiene
que guardar esa copia en otro DataNode.
"""

import os

from helpers import MB, Results, dn_block_ids, get, ls, put, start_dn, stop_dn, unique, user

CONFIG = {"HEALTH_INTERVAL": 300, "LOCK_TIMEOUT": 15}


def main() -> None:
    t = Results()
    beto = unique("beto")
    hb = user(beto)
    home = f"/home/{beto}"
    viejo = os.urandom(3 * MB)
    t.check("PUT inicial con los 3 DataNodes", put(hb, home, "a.bin", viejo).status_code == 201)

    t.section("DN3 se cae y el ControlNode todavía lo cree ONLINE")
    stop_dn(3)
    c1, c2 = len(dn_block_ids(1)), len(dn_block_ids(2))
    nuevo = os.urandom(int(4.5 * MB))
    r = put(hb, home, "b.bin", nuevo)
    t.check("el PUT de 5 bloques igual se completa (cada copia que iba a DN3 va a otro nodo)",
            r.status_code == 201 and r.json()["block_count"] == 5, r.text)
    nuevas = len(dn_block_ids(1)) - c1 + len(dn_block_ids(2)) - c2
    t.check("las 10 copias quedaron en DN1 y DN2", nuevas == 10, nuevas)
    t.check("el archivo nuevo se descarga idéntico", get(hb, f"{home}/b.bin").content == nuevo)

    t.section("Solo queda DN1: el overwrite falla y la versión vieja se mantiene")
    stop_dn(2)
    r = put(hb, home, "a.bin", os.urandom(2 * MB), overwrite=True)
    t.check("overwrite sin poder lograr factor 2 -> 422", r.status_code == 422, r.text)
    start_dn(2)
    start_dn(3)
    r = get(hb, f"{home}/a.bin")
    t.check("la versión vieja sigue intacta", r.status_code == 200 and r.content == viejo, r.status_code)
    r = put(hb, home, "a.bin", nuevo, overwrite=True)
    t.check("con los nodos de vuelta, el overwrite funciona",
            r.status_code == 201 and get(hb, f"{home}/a.bin").content == nuevo, r.text)

    t.section("Un PUT nuevo que falla no deja el nombre ocupado")
    stop_dn(2)
    stop_dn(3)
    r = put(hb, home, "c.bin", os.urandom(MB))
    t.check("PUT nuevo sin poder lograr factor 2 -> 422", r.status_code == 422, r.text)
    t.check("c.bin no aparece en ls", "c.bin" not in ls(hb, home))
    start_dn(2)
    start_dn(3)
    t.check("se puede volver a subir c.bin sin overwrite", put(hb, home, "c.bin", b"ya").status_code == 201)
    t.finish()


if __name__ == "__main__":
    main()

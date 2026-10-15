# Pruebas de DFSha

Pruebas de punta a punta: levantan un clúster real de DFSha en Docker
(PostgreSQL, ControlNode y 3 DataNodes) y lo prueban a través de su API, igual
que lo usaría el cliente. Son las pruebas cuyos resultados aparecen en el
informe.

## Cómo correrlas

Requisitos: Docker Desktop abierto y Python 3.11 o superior.

Desde la raíz del repositorio:

```powershell
python -m pip install -r tests/requirements.txt
python tests/run_all.py
```

Al final se ve un resumen como este:

```
Suite                               Pasaron  Fallaron    Tiempo
test_01_get_rm                           33         0       50s
...
TOTAL                                   ...         0

TODO EN VERDE
```

Otras formas de correrlas:

| Comando | Qué hace |
| --- | --- |
| `python tests/run_all.py permisos` | Corre solo las suites cuyo nombre contiene "permisos" |
| `python tests/run_all.py get_rm sesiones` | Corre varias suites por nombre |
| `python tests/run_all.py --keep` | No borra el clúster de pruebas al terminar (para revisarlo) |

La primera vez construye las imágenes de Docker y tarda unos minutos más. La
corrida completa tarda unos 6 a 8 minutos.

## Qué hace `run_all.py`

1. Construye las imágenes con `tests/docker-compose.yml`.
2. Para **cada** suite borra el clúster de pruebas y lo levanta de cero (base
   de datos vacía, DataNodes sin bloques), con los tiempos que esa suite
   necesita.
3. Corre la suite y muestra cada verificación (`[PASA]` / `[FALLA]`).
4. Al final borra el clúster de pruebas y muestra el resumen.

El clúster de pruebas está **aislado**: usa el proyecto de Docker
`dfsha-test`, los puertos 18000 a 18003 y claves fijas de prueba. No toca el
entorno de desarrollo (`docker-compose.yml` de la raíz) ni el despliegue en AWS.
Para que las pruebas usen archivos pequeños y sean rápidas, ese clúster usa
bloques de **1 MB** (en vez de 64 MB) y tiempos cortos: chequeo de DataNodes
cada 1 s, lock de 5 s, limpieza cada 2 s.

## Suites

| Suite | Qué prueba |
| --- | --- |
| `test_01_get_rm.py` | GET íntegro; GET con un DataNode apagado (usa la otra copia); copias dañadas (detecta el SHA-256 malo y usa la otra copia); 503 si no queda ninguna copia; GET concurrentes; limpieza de temporales; rm, rm con un nodo caído y su limpieza posterior; nombre libre después de un borrado o una subida fallida |
| `test_02_sesiones.py` | open, read (rangos que cruzan bloques), close; WRITE LOCK (423) y su vencimiento (410); dos open WRITE a la vez; write en medio y al final del archivo (versión nueva); borrado de versiones viejas; write que no puede lograr factor 2 deja intacta la versión anterior |
| `test_03_overwrite.py` | PUT repetido (409); PUT con overwrite (de 3 a 5 bloques y de 5 a 1); borrado de la versión vieja; 423 con el archivo bloqueado; 403 sin permiso de escritura; se conservan dueño y ACL |
| `test_04_put_con_fallas.py` | Un DataNode cae sin que el monitor lo note todavía: cada copia que iba a él se guarda en otro nodo; si no se puede lograr factor 2, el PUT y el overwrite fallan sin dejar rastro y la versión anterior queda intacta |
| `test_05_permisos.py` | chmod, chgrp, ACL de usuario y de grupo, grupos por nombre y por id, solo el dueño cambia permisos, el dueño nunca queda bloqueado en lo suyo, nadie se agrega solo a un grupo ajeno, las ACL se borran con su recurso |
| `test_06_redundancia_cli.py` | Bloques que quedaron con una sola copia se restauran solos; copias huérfanas de un nodo que vuelve se borran (y las recientes se respetan); comandos del cliente CLI |
| `test_07_rereplicacion.py` | Al caer un DataNode se recuperan todas sus copias (80 de 80) y el ControlNode sigue respondiendo mientras tanto |

La prueba del despliegue en AWS es aparte: `deploy/prueba-despliegue.sh` (ver
`deploy/README.md`).

## Si algo falla

- **Docker no está abierto:** `run_all.py` falla al construir. Abre Docker
  Desktop y espera a que diga "Engine running".
- **Puerto ocupado (18000 a 18003):** algo más está usando esos puertos.
  Ciérralo o detén el clúster de pruebas anterior con
  `docker compose -f tests/docker-compose.yml down -v`.
- **Ver los logs del ControlNode de pruebas:** corre con `--keep` y después
  `docker compose -f tests/docker-compose.yml logs control_node`.

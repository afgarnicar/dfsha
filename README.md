# DFSha — Distributed File System with High Availability

Proyecto 1 — ST0263/SI3007 Sistemas Distribuidos, EAFIT, 2026-2.

DFSha es un sistema de archivos distribuido por bloques, inspirado en HDFS. Un
**ControlNode** guarda los metadatos y coordina; tres o más **DataNodes**
guardan los bloques; un **cliente CLI** permite subir, bajar y administrar
archivos. Cada bloque se cifra y se guarda dos veces (PRIMARY y BACKUP) en
DataNodes distintos, así que el sistema sigue funcionando si un DataNode cae.

- Guía de despliegue en AWS Academy (4 VMs): [`deploy/README.md`](deploy/README.md).
- Pruebas de punta a punta: [`tests/README.md`](tests/README.md).
- Documentación interactiva de la API: `https://<host>/docs` (Swagger de FastAPI).

---

## Arquitectura

```
                 HTTPS (443)                       HTTPS interno (8000)
 Cliente CLI ─────────────────► Nginx ──► ControlNode ─────────────────► DataNode DN1
 (python -m client.cli)         (TLS)     │ FastAPI                ┌───► DataNode DN2
                                          │ JWT, permisos, locks   └───► DataNode DN3
                                          ▼                              ▲   │
                                      PostgreSQL                         └───┘
                                      (metadatos)              re-replicación DataNode → DataNode
```

| Componente | Qué hace |
|---|---|
| **ControlNode** (`control_node/`) | Autenticación (JWT), árbol de directorios, permisos (Unix + ACL + grupos), locks, distribución Round Robin, cifrado de bloques, monitoreo de DataNodes, re-replicación y limpieza. Es el único que habla con el cliente. |
| **DataNode** (`data_node/`) | Guarda, entrega, borra y replica bloques. Es deliberadamente simple: no cifra ni conoce archivos, solo bloques con un id opaco y su SHA-256. |
| **Cliente** (`client/`) | CLI hecha con Typer. Guarda la sesión en `~/.dfsha/session.json`. |
| **Nginx** (`nginx/`) | Termina HTTPS hacia el cliente y redirige el puerto 80 a 443. |
| **PostgreSQL** | Metadatos: usuarios, grupos, directorios, archivos, bloques, réplicas, handles y locks (migraciones con Alembic). |
| **shared/** | Constantes, enums y el certificado interno, comunes a ControlNode y DataNode. |

### Decisiones principales

| Tema | Decisión |
|---|---|
| Tamaño de bloque | 64 MB (`BLOCK_SIZE_MB`). |
| Replicación | Factor 2 obligatorio: PRIMARY + BACKUP en nodos distintos. Si no hay 2 DataNodes elegibles, la operación se rechaza; nunca se guarda con factor 1. |
| Distribución | Round Robin entre los DataNodes ONLINE con espacio suficiente. |
| Fallo de una réplica | Se reintenta hasta `MAX_RETRIES` veces, probando primero otros DataNodes elegibles. |
| Cifrado en reposo | Fernet (AES-128-CBC + HMAC) en el ControlNode. Los DataNodes solo ven bloques cifrados. El SHA-256 se calcula sobre el bloque cifrado y se valida en cada lectura. |
| Monitoreo | Health check cada `HEALTH_CHECK_INTERVAL_SECONDS`. Estados ONLINE → SUSPECT → OFFLINE (tras `DATANODE_FAILURE_THRESHOLD` fallos). |
| Re-replicación | Cuando un DataNode pasa a OFFLINE, sus bloques se copian de DataNode a DataNode hacia otro nodo, en segundo plano. Una limpieza periódica reintenta lo que no se pudo copiar. |
| Lectura (GET) | Descarga los bloques en paralelo (`MAX_PARALLEL_DOWNLOADS`), usa el BACKUP si el PRIMARY falla, valida el checksum, descifra y reconstruye en un temporal con control de espacio. |
| Escritura | `PUT` crea; `PUT --overwrite` y `write` reemplazan el archivo completo como una **versión nueva**: solo cuando todos sus bloques están guardados con factor 2 se activa, y la vieja se borra. Si algo falla, la versión anterior queda intacta. |
| Concurrencia | WRITE LOCK exclusivo por archivo completo, con vencimiento (`LOCK_TIMEOUT_SECONDS`) y renovación en cada escritura. |
| Permisos | Bits rwx Unix (dueño, grupo, otros) + ACL por usuario y por grupo. Se evalúa como en las ACL POSIX: dueño, ACL del usuario, ACL de sus grupos, grupo, otros. En directorios: r = listar, w = crear/borrar, x = atravesar. Solo el dueño cambia permisos; solo los miembros de un grupo agregan miembros. |
| Seguridad de red | Cliente → Nginx por HTTPS. ControlNode ↔ DataNodes y DataNode ↔ DataNode por HTTPS con un certificado derivado de `INTERNAL_API_KEY`, más la cabecera `X-DFSHA-API-Key`. |

## Estructura del repositorio

```
client/            Cliente CLI (Typer)
control_node/      ControlNode: app/api, app/services, app/repositories, app/models, migrations/
data_node/         DataNode: app/api, app/storage, app/services y run.py (arranque con HTTPS)
shared/            Constantes, enums y certificado interno (internal_tls.py)
nginx/             nginx.conf y certificado autofirmado de pruebas
deploy/            Despliegue en AWS (docker-compose por tipo de VM, scripts y guía)
tests/             Pruebas de punta a punta contra un clúster de pruebas en Docker
docker-compose.yml Entorno local completo: Postgres + ControlNode + 3 DataNodes + Nginx
```

---

## Ejecutar en local (Docker Desktop)

Requisitos: Docker Desktop (con Docker Compose) y Python 3.11 o superior para el
cliente. Los comandos son para PowerShell, desde la carpeta del repositorio.

1. Crear el `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

   Abre `.env` y cambia `JWT_SECRET` e `INTERNAL_API_KEY` por textos largos y
   aleatorios.

2. Generar la clave de cifrado de bloques y guardarla en el `.env`:

   ```powershell
   docker compose build
   $key = docker compose run --rm --no-deps control_node python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   (Get-Content .env) -replace '^ENCRYPTION_KEY=.*', "ENCRYPTION_KEY=$key" | Set-Content .env
   ```

   > No cambies la `ENCRYPTION_KEY` después de subir archivos: lo cifrado con la
   > clave anterior ya no se podría leer.

3. Crear las tablas (migraciones) y levantar todo:

   ```powershell
   docker compose run --rm control_node alembic upgrade head
   docker compose up -d
   ```

4. Comprobar:

   ```powershell
   curl.exe -k https://localhost/health
   curl.exe -k https://localhost/datanodes
   ```

   Los 3 DataNodes deben aparecer `ONLINE`. La API queda documentada en
   `https://localhost/docs`.

Para apagar: `docker compose down` (con `-v` también se borran la base de datos
y los bloques).

## Cliente CLI

```powershell
pip install -r client/requirements.txt
python -m client.cli set-url https://localhost
python -m client.cli auth register beto      # pide la contraseña
python -m client.cli auth login beto
```

| Comando | Qué hace |
|---|---|
| `auth register <usuario>` / `auth login <usuario>` / `auth logout` / `auth me` | Cuentas y sesión. Al registrarse se crea `/home/<usuario>`. |
| `fs mkdir <ruta>` / `fs rmdir <ruta>` | Crear o borrar (vacío) un directorio. |
| `fs ls [ruta]` / `fs cd <ruta>` | Listar un directorio / validar que se puede entrar. |
| `files put <archivo-local> <directorio> [--overwrite]` | Subir un archivo. Con `--overwrite` reemplaza uno existente de forma segura. |
| `files get <ruta> [-o destino]` | Descargar un archivo (`-o -` lo muestra en pantalla). |
| `files rm <ruta>` | Borrar un archivo y sus bloques. |
| `files open <ruta> --mode read\|write` | Abrir un archivo y obtener un handle (en `write` toma el lock). |
| `files read <handle> <offset> <longitud>` | Leer un rango de bytes. |
| `files write <handle> <offset> (--text T \| --from F)` | Escribir desde un offset (reemplazo seguro por versión nueva). |
| `files close <handle>` | Cerrar el handle y liberar el lock. |
| `access info <ruta>` | Ver dueño, grupo, permisos y ACL. |
| `access chmod <modo> <ruta>` | Cambiar permisos, por ejemplo `750`. |
| `access chgrp <grupo\|-> <ruta>` | Asociar (o quitar) un grupo. |
| `access acl-set user:<u>\|group:<g> <rwx> <ruta>` / `access acl-rm ...` | Dar o quitar permisos específicos. |
| `access group-create <grupo>` / `access group-add <grupo> <usuario>` | Crear grupos y agregar miembros. |

Ejemplo completo:

```powershell
python -m client.cli fs mkdir /home/beto/proyecto
python -m client.cli files put .\informe.pdf /home/beto/proyecto
python -m client.cli files put .\informe.pdf /home/beto/proyecto --overwrite
python -m client.cli fs ls /home/beto/proyecto
python -m client.cli files get /home/beto/proyecto/informe.pdf -o copia.pdf
python -m client.cli access acl-set user:juan r-- /home/beto/proyecto/informe.pdf
```

## API del ControlNode

Todas las rutas (salvo `/health`, `/auth/register`, `/auth/login` y
`/datanodes`) exigen `Authorization: Bearer <token>`.

| Método y ruta | Uso |
|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | Usuarios y JWT. |
| `POST /directories`, `DELETE /directories` | mkdir / rmdir. |
| `GET /filesystem/list?path=`, `GET /filesystem/resolve?path=` | ls / cd. |
| `POST /files` (multipart: `directory_path`, `file`, `overwrite`) | PUT. 201, 409 si existe sin overwrite, 423 si está bloqueado, 422 si no hay factor 2. |
| `GET /files/download?path=` | GET en streaming. |
| `DELETE /files` `{path}` | rm. |
| `POST /files/open` `{path, mode}` | open → handle. 423 si otro tiene el lock. |
| `POST /files/read` `{handle, offset, length}` | read. |
| `POST /files/write` (multipart: `handle`, `offset`, `file`) | write. |
| `POST /files/close` `{handle}` | close. |
| `GET /access?path=` | Permisos, dueño, grupo y ACL. |
| `PATCH /access/mode`, `PATCH /access/group` | chmod / chgrp (solo el dueño). |
| `PUT /access/acl`, `DELETE /access/acl` | Crear/cambiar o quitar una ACL. |
| `POST /access/groups`, `POST /access/groups/{nombre}/members` | Grupos por nombre. Quien crea un grupo queda como miembro; solo un miembro puede agregar a otros. |
| `POST /groups`, `GET /groups`, `POST /groups/{id}/members` | Lo mismo, por id de grupo y de usuario. |
| `GET /datanodes`, `GET /health` | Estado de los DataNodes y del ControlNode. |

Los DataNodes exponen `POST /blocks`, `GET /blocks`, `GET /blocks/{id}`,
`DELETE /blocks/{id}`, `POST /blocks/{id}/replicate` y `GET /health`; todos
(salvo `/health`) exigen la cabecera `X-DFSHA-API-Key`.

## Pruebas

Las pruebas levantan un clúster de pruebas aislado en Docker (proyecto
`dfsha-test`, puertos 18000 a 18003) y prueban el sistema a través de su API.
Con Docker Desktop abierto, desde la raíz del repositorio:

```powershell
python -m pip install -r tests/requirements.txt
python tests/run_all.py
```

Son 7 suites: GET y rm, sesiones y WRITE LOCK, overwrite, PUT con fallas,
permisos, redundancia y CLI, y re-replicación. El detalle está en
[`tests/README.md`](tests/README.md). La prueba del despliegue en AWS es
`deploy/prueba-despliegue.sh`.

## Configuración

Todo se configura por variables de entorno (ver `.env.example`):

| Variable | Valor por defecto | Para qué |
|---|---|---|
| `BLOCK_SIZE_MB` | 64 | Tamaño de bloque. |
| `REPLICATION_FACTOR` | 2 | Réplicas por bloque. |
| `MAX_RETRIES` | 3 | Intentos por réplica (cambiando de nodo). |
| `MAX_PARALLEL_DOWNLOADS` | 8 | Bloques descargados en paralelo en un GET. |
| `LOCK_TIMEOUT_SECONDS` | 30 | Vencimiento del WRITE LOCK. |
| `HANDLE_TIMEOUT_SECONDS` | 300 | Vencimiento de un handle de lectura sin uso. |
| `HEALTH_CHECK_INTERVAL_SECONDS` / `DATANODE_FAILURE_THRESHOLD` | 10 / 3 | Monitoreo de DataNodes. |
| `TEMP_ROOT` / `TEMP_SPACE_MARGIN_PERCENT` | /tmp/dfsha / 10 | Temporal del ControlNode para reconstruir archivos. |
| `CLEANUP_INTERVAL_SECONDS` / `ORPHAN_GRACE_SECONDS` | 30 / 900 | Limpieza periódica y edad mínima de una copia huérfana. |
| `ENCRYPTION_KEY` | — | Clave Fernet de los bloques. |
| `JWT_SECRET`, `JWT_EXPIRATION` | — | Tokens de usuario. |
| `INTERNAL_API_KEY` | — | Clave entre nodos; de ella se deriva el certificado del HTTPS interno. |
| `INTERNAL_TLS` | true | HTTPS entre nodos (`false` solo para depurar). |
| `DATANODES` | — | `DN1:host:puerto,...` que el ControlNode registra al arrancar. |

## Limpieza y tolerancia a fallos

Un proceso periódico del ControlNode (`CleanupWorker`):

- libera handles y locks vencidos;
- termina borrados pendientes (`rm` con un DataNode caído) y subidas fallidas
  (también las que quedaron a medias si el ControlNode se reinició);
- borra versiones viejas de archivos reemplazados;
- restaura el factor 2 de bloques que quedaron con una sola réplica;
- borra de los DataNodes las copias que los metadatos ya no registran (huérfanas).

## Limitaciones conocidas

- `write` y `PUT --overwrite` reemplazan el archivo completo (no se modifican
  bloques sueltos); no se permiten huecos (offset mayor que el tamaño).
- El lock es del archivo completo, no por bloque.
- El ControlNode y su PostgreSQL son un único punto de coordinación (no están
  replicados); la alta disponibilidad es de los datos (DataNodes).
- El certificado de Nginx es autofirmado: el cliente no lo verifica.

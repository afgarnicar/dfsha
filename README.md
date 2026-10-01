# DFSha — Distributed File System with High Availability

Proyecto 1 — ST0263/SI3007 Sistemas Distribuidos, 2026-2

## Arquitectura

- **ControlNode**: cerebro del sistema. Maneja metadatos, autenticación, distribución y replicación.
- **DataNode**: almacena bloques físicos. Deliberadamente simple.
- **Cliente**: CLI para subir, bajar y gestionar archivos.

## Stack

Python 3.12 · FastAPI · PostgreSQL · SQLAlchemy · Alembic · Docker · Nginx

## Requisitos

- Docker Desktop
- Docker Compose

## Levantar el sistema

```bash
cp .env.example .env
docker compose up --build
```

## Variables de entorno

Ver `.env.example` para la configuración completa.
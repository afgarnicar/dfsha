"""Motor de base de datos, fábrica de sesiones y espera de disponibilidad.

Este módulo concentra la creación del engine de SQLAlchemy y expone una
dependencia de sesión para FastAPI. También ofrece una espera activa a que
PostgreSQL esté listo, porque el orden de arranque de Docker no garantiza que
la base de datos acepte conexiones cuando el ControlNode inicia.
"""

import logging
import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# El engine mantiene el pool de conexiones hacia PostgreSQL. ``pool_pre_ping``
# evita usar conexiones que el servidor ya cerró.
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)

# Fábrica de sesiones. Cada petición obtiene su propia sesión y la cierra al final.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def wait_for_database(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Espera hasta que PostgreSQL acepte conexiones.

    Reintenta la conexión varias veces porque la base de datos puede tardar en
    estar lista aunque su contenedor ya haya arrancado. Lanza una excepción si
    se agotan los intentos.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            logger.info("Conexión con la base de datos establecida.")
            return
        except OperationalError as error:
            logger.warning(
                "La base de datos no está lista (intento %d/%d): %s",
                attempt,
                max_attempts,
                error,
            )
            time.sleep(delay_seconds)

    raise RuntimeError(
        "No fue posible conectar con la base de datos tras "
        f"{max_attempts} intentos."
    )


def get_session() -> Generator[Session, None, None]:
    """Dependencia de FastAPI que entrega una sesión y garantiza su cierre."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

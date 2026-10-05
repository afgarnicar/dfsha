"""Clase base declarativa compartida por todos los modelos de SQLAlchemy.

Todos los modelos del ControlNode heredan de ``Base`` para que Alembic y el
``MetaData`` central conozcan todas las tablas al generar migraciones.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa de la que heredan todos los modelos de metadatos."""

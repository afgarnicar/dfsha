"""Entorno de ejecución de las migraciones de Alembic.

Toma la URL de la base de datos desde la configuración del ControlNode (variable
de entorno DATABASE_URL) y usa la metadata de los modelos para habilitar la
generación automática de migraciones.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings

# Importar el paquete de modelos registra todas las tablas en Base.metadata.
from app.models import Base

# Objeto de configuración de Alembic (lee alembic.ini).
config = context.config

# Configurar el logging definido en alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inyectar la URL de la base de datos desde el entorno, sin credenciales en archivos.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# Metadata de destino para la autogeneración de migraciones.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Ejecuta las migraciones en modo 'offline' (genera SQL sin conectar)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Ejecuta las migraciones en modo 'online' (conectado a la base de datos)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

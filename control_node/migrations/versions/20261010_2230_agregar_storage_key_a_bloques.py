"""agregar storage_key a bloques

Guarda en cada bloque el nombre con el que quedó almacenado en los DataNodes
(el block_id que reciben en POST /blocks). Sin este dato, el ControlNode no
puede pedir, borrar ni replicar los bloques de un archivo.

Los bloques que ya existían antes de esta migración no tienen ese nombre
guardado, así que no se pueden recuperar. Se les asigna un valor
``legacy-<id>`` solo para poder marcar la columna como obligatoria y única.

Revision ID: 7c1e4a9b2d10
Revises: 42151b55aae0
Create Date: 2026-10-10 22:30:00.000000+00:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de la revisión, usados por Alembic.
revision: str = '7c1e4a9b2d10'
down_revision: str | None = '42151b55aae0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agrega la columna storage_key, la rellena en filas viejas y la vuelve obligatoria."""
    with op.batch_alter_table('blocks') as batch_op:
        batch_op.add_column(sa.Column('storage_key', sa.String(length=64), nullable=True))

    # Filas creadas antes de esta migración: valor de relleno único.
    op.execute("UPDATE blocks SET storage_key = 'legacy-' || id WHERE storage_key IS NULL")

    with op.batch_alter_table('blocks') as batch_op:
        batch_op.alter_column('storage_key', existing_type=sa.String(length=64), nullable=False)
        batch_op.create_unique_constraint('uq_blocks_storage_key', ['storage_key'])


def downgrade() -> None:
    """Revierte los cambios de esta revisión."""
    with op.batch_alter_table('blocks') as batch_op:
        batch_op.drop_constraint('uq_blocks_storage_key', type_='unique')
        batch_op.drop_column('storage_key')

"""Repositorio de acceso a datos de los DataNodes.

Encapsula todas las consultas y escrituras sobre la tabla ``datanodes``, de
modo que la lógica de negocio (los servicios) no dependa directamente de la
API de SQLAlchemy. Esto facilita las pruebas y mantiene las responsabilidades
separadas.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.storage import DataNode
from shared.enums import NodeStatus


class DataNodeRepository:
    """Operaciones de persistencia sobre los DataNodes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_name(self, name: str) -> DataNode | None:
        """Devuelve el DataNode con el nombre dado, o None si no existe."""
        statement = select(DataNode).where(DataNode.name == name)
        return self._session.scalars(statement).first()

    def list_all(self) -> Sequence[DataNode]:
        """Devuelve todos los DataNodes registrados, ordenados por nombre."""
        statement = select(DataNode).order_by(DataNode.name)
        return self._session.scalars(statement).all()

    def create(self, name: str, host: str, port: int) -> DataNode:
        """Crea un DataNode nuevo en estado OFFLINE.

        El nodo arranca OFFLINE porque todavía no se ha verificado su salud;
        será el monitor de nodos (etapa posterior) quien lo marque ONLINE.
        """
        datanode = DataNode(
            name=name,
            host=host,
            port=port,
            status=NodeStatus.OFFLINE,
            free_space_bytes=0,
            failure_count=0,
        )
        self._session.add(datanode)
        return datanode

    def update_endpoint(self, datanode: DataNode, host: str, port: int) -> DataNode:
        """Actualiza el host y el puerto de un DataNode existente."""
        datanode.host = host
        datanode.port = port
        return datanode

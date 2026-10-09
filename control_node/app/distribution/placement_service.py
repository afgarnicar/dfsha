"""Colocación de réplicas de un bloque con factor de replicación 2.

Dado un bloque y los DataNodes disponibles, decide en qué dos nodos distintos
se guardará: uno como PRIMARY y otro como BACKUP. Solo considera nodos
elegibles (en línea y con espacio suficiente) y usa una estrategia de
distribución para ordenarlos.

Este servicio solo DECIDE la colocación; no escribe en la base de datos ni envía
bytes a los DataNodes. La persistencia y el envío son parte de la subida (PUT).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.distribution.strategy import CandidateNode, DistributionStrategy
from shared.enums import NodeStatus, ReplicaRole

# Cantidad de réplicas que debe tener cada bloque (PRIMARY + BACKUP).
REPLICATION_FACTOR = 2


@dataclass(frozen=True)
class EligibleNode:
    """DataNode candidato con sus datos relevantes para la elegibilidad."""

    datanode_id: int
    name: str
    status: NodeStatus
    free_space_bytes: int


@dataclass(frozen=True)
class ReplicaPlacement:
    """Asignación de una réplica a un DataNode con un rol concreto."""

    datanode_id: int
    name: str
    role: ReplicaRole


class NotEnoughEligibleNodesError(Exception):
    """No hay suficientes DataNodes elegibles para garantizar el factor de replicación."""

    def __init__(self, available: int, required: int) -> None:
        super().__init__(
            f"se requieren {required} DataNodes elegibles y solo hay {available}"
        )
        self.available = available
        self.required = required


def is_eligible(node: EligibleNode, block_size_bytes: int) -> bool:
    """Indica si un DataNode puede recibir un bloque de cierto tamaño.

    Un nodo es elegible si está EN LÍNEA y tiene al menos tanto espacio libre
    como el tamaño del bloque.
    """
    return node.status == NodeStatus.ONLINE and node.free_space_bytes >= block_size_bytes


class PlacementService:
    """Decide la colocación de las réplicas de un bloque."""

    def __init__(
        self,
        strategy: DistributionStrategy,
        replication_factor: int = REPLICATION_FACTOR,
    ) -> None:
        self._strategy = strategy
        self._replication_factor = replication_factor

    def place_block(
        self,
        nodes: Sequence[EligibleNode],
        block_index: int,
        block_size_bytes: int,
    ) -> list[ReplicaPlacement]:
        """Elige los DataNodes para las réplicas de un bloque y les asigna roles.

        Devuelve una lista con una colocación por réplica, en orden de rol
        (PRIMARY primero, BACKUP después). Lanza NotEnoughEligibleNodesError si
        no hay suficientes nodos elegibles para garantizar el factor de
        replicación, en vez de degradarlo en silencio.
        """
        eligible = [node for node in nodes if is_eligible(node, block_size_bytes)]
        if len(eligible) < self._replication_factor:
            raise NotEnoughEligibleNodesError(
                available=len(eligible), required=self._replication_factor
            )

        candidates = [
            CandidateNode(
                datanode_id=node.datanode_id,
                name=node.name,
                free_space_bytes=node.free_space_bytes,
            )
            for node in eligible
        ]
        ordered = self._strategy.order_candidates(candidates, block_index)

        # Los primeros nodos del orden reciben las réplicas. El primero es
        # PRIMARY; los siguientes, BACKUP. Como todos son nodos distintos, nunca
        # se coloca PRIMARY y BACKUP del mismo bloque en el mismo DataNode.
        roles = [ReplicaRole.PRIMARY] + [ReplicaRole.BACKUP] * (
            self._replication_factor - 1
        )
        chosen = ordered[: self._replication_factor]
        return [
            ReplicaPlacement(datanode_id=node.datanode_id, name=node.name, role=role)
            for node, role in zip(chosen, roles)
        ]

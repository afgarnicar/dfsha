"""Estrategias de distribución de bloques entre DataNodes.

La estrategia decide el ORDEN en que se consideran los DataNodes candidatos
para colocar un bloque. Está desacoplada del resto del ControlNode detrás de la
interfaz ``DistributionStrategy``, para poder cambiar el criterio (por ejemplo a
una basada en espacio libre o en carga) sin modificar la lógica de colocación
ni la de subida.

La estrategia inicial es Round Robin determinista: no guarda estado entre
llamadas, sino que rota según el índice del bloque. Así los bloques de un
archivo se reparten de forma pareja (B0->DN1, B1->DN2, B2->DN3, B3->DN1, ...),
y el resultado es reproducible y fácil de probar.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateNode:
    """DataNode candidato para colocar una réplica.

    Es una vista mínima del DataNode, independiente del modelo de base de datos,
    para que las estrategias se puedan probar sin tocar PostgreSQL.
    """

    datanode_id: int
    name: str
    free_space_bytes: int


class DistributionStrategy(ABC):
    """Interfaz de una estrategia de distribución de bloques."""

    @abstractmethod
    def order_candidates(
        self, candidates: Sequence[CandidateNode], block_index: int
    ) -> list[CandidateNode]:
        """Devuelve los candidatos en el orden de preferencia para el bloque dado.

        El primero de la lista es el preferido para la réplica PRIMARY, el
        segundo para la BACKUP, y así sucesivamente.
        """
        raise NotImplementedError


class RoundRobinStrategy(DistributionStrategy):
    """Reparte los bloques de forma rotativa y determinista por índice de bloque."""

    def order_candidates(
        self, candidates: Sequence[CandidateNode], block_index: int
    ) -> list[CandidateNode]:
        if not candidates:
            return []
        # La rotación arranca en un nodo distinto para cada bloque, de modo que
        # bloques consecutivos empiecen en nodos consecutivos.
        start = block_index % len(candidates)
        ordered = list(candidates[start:]) + list(candidates[:start])
        return ordered

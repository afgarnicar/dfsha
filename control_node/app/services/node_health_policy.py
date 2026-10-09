"""Regla de transición de estados de un DataNode.

Se separa en una función pura (sin base de datos ni red) para que la regla sea
fácil de leer y de probar por sí sola:

- Si la verificación sale bien, el nodo queda ONLINE y su contador vuelve a 0.
- Si falla, se suma 1 al contador de fallos consecutivos:
  - si llega al umbral, el nodo pasa a OFFLINE;
  - si no llega, el nodo queda SUSPECT.
- Un nodo que ya estaba OFFLINE y sigue fallando se queda OFFLINE (no "mejora"
  a SUSPECT por fallar).
- Un nodo OFFLINE que vuelve a responder bien pasa directamente a ONLINE.
"""

from dataclasses import dataclass

from shared.enums import NodeStatus


@dataclass(frozen=True)
class NodeHealthState:
    """Estado de salud de un nodo: su estado y sus fallos consecutivos."""

    status: NodeStatus
    failure_count: int


def next_health_state(
    current: NodeHealthState, check_ok: bool, failure_threshold: int
) -> NodeHealthState:
    """Calcula el nuevo estado de un nodo a partir del resultado de una verificación."""
    if check_ok:
        return NodeHealthState(status=NodeStatus.ONLINE, failure_count=0)

    failures = current.failure_count + 1
    if current.status == NodeStatus.OFFLINE or failures >= failure_threshold:
        return NodeHealthState(status=NodeStatus.OFFLINE, failure_count=failures)
    return NodeHealthState(status=NodeStatus.SUSPECT, failure_count=failures)

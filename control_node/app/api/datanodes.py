"""Endpoints de la API relacionados con los DataNodes."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.datanode import DataNodeResponse
from app.services.datanode_service import DataNodeRegistryService

router = APIRouter(prefix="/datanodes", tags=["datanodes"])


@router.get("", response_model=list[DataNodeResponse])
def list_datanodes(session: Session = Depends(get_session)) -> list[DataNodeResponse]:
    """Lista todos los DataNodes registrados en el ControlNode.

    Endpoint de solo lectura, útil para verificar el estado del registro.
    """
    service = DataNodeRegistryService(session)
    datanodes = service.list_datanodes()
    return [DataNodeResponse.model_validate(node) for node in datanodes]

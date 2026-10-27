"""Rule-based traffic assistant endpoint."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import TrafficAssistantRequest
from backend.services.traffic_assistant_service import answer_traffic_question

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/traffic-assistant", tags=["traffic assistant"])


@router.post("")
def ask_traffic_assistant(request: TrafficAssistantRequest, db: Session = Depends(get_db)):
    try:
        return answer_traffic_question(db, request.message, request.action)
    except SQLAlchemyError as exc:
        logger.exception("Could not retrieve traffic data for the assistant")
        raise HTTPException(
            status_code=503,
            detail="Traffic assistant data is temporarily unavailable; please try again.",
        ) from exc

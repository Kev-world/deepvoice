"""API routes for the custom LiveKit meeting room platform."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meet", tags=["meet"])

VOCAL_BRIDGE_TOKEN_URL = "https://vocalbridgeai.com/api/v1/token"
AGENT_NAME = "deepvoice-orangutan"


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class MeetTokenRequest(BaseModel):
    """Request body for generating a LiveKit meeting token."""

    room_name: str = Field(..., description="Name of the LiveKit room to join.")
    participant_name: str = Field(..., description="Display name of the participant.")


class MeetTokenResponse(BaseModel):
    """Response containing the LiveKit connection details."""

    livekit_url: str
    token: str
    room_name: str
    participant_identity: str


class MeetStatusResponse(BaseModel):
    """Response indicating whether the meeting service is available."""

    available: bool
    agent_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/token", response_model=MeetTokenResponse)
async def generate_meet_token(body: MeetTokenRequest) -> MeetTokenResponse:
    """Generate a LiveKit token for a participant to join a room.

    Calls the Vocal Bridge API which automatically creates or reuses rooms.
    The AI agent joins the room when a participant connects.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                VOCAL_BRIDGE_TOKEN_URL,
                headers={
                    "X-API-Key": settings.vocal_bridge_api_key,
                    "Content-Type": "application/json",
                },
                json={"participant_name": body.participant_name},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Vocal Bridge API returned %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Vocal Bridge API error: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        logger.exception("Failed to reach Vocal Bridge API")
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Vocal Bridge API: {exc}",
        )

    data = response.json()

    return MeetTokenResponse(
        livekit_url=data.get("livekit_url", ""),
        token=data.get("token", ""),
        room_name=data.get("room_name", body.room_name),
        participant_identity=data.get("participant_identity", body.participant_name),
    )


@router.get("/status", response_model=MeetStatusResponse)
async def meet_status() -> MeetStatusResponse:
    """Check whether the meeting service is available."""
    return MeetStatusResponse(available=True, agent_name=AGENT_NAME)

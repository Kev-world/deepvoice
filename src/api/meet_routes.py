"""API routes for the custom LiveKit meeting room platform."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meet", tags=["meet"])

VOCAL_BRIDGE_TOKEN_URL = "https://vocalbridgeai.com/api/v1/token"
AGENT_NAME = "deepvoice-orangutan"

# In-memory room registry: maps our room code → VB session info.
_rooms: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class MeetTokenRequest(BaseModel):
    room_name: str = Field(..., description="Our room code (e.g. abc-defg-hij).")
    participant_name: str = Field(..., description="Display name of the participant.")
    agent_id: str = Field(
        default="orchestrator",
        description="Which agent to use (orchestrator, frontend, backend).",
    )


class MeetTokenResponse(BaseModel):
    livekit_url: str
    token: str
    room_name: str
    participant_identity: str


class MeetStatusResponse(BaseModel):
    available: bool
    agent_name: str


class RoomInfoResponse(BaseModel):
    room_code: str
    livekit_room: str | None = None
    participants: int
    created_at: float
    invite_link: str


class ActiveRoomsResponse(BaseModel):
    rooms: list[RoomInfoResponse]


class HandoffRequest(BaseModel):
    room_code: str = Field(..., description="Current room code")
    agent_id: str = Field(..., description="Target agent (frontend, backend)")
    participant_name: str = Field(default="Guest", description="Participant name")
    context: str = Field(default="", description="Context about why we're handing off")


class HandoffResponse(BaseModel):
    livekit_url: str
    token: str
    room_name: str
    participant_identity: str
    agent_id: str
    agent_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _request_vb_token(
    participant_name: str,
    api_key: str,
    room_code: str | None = None,
) -> dict:
    """Call the Vocal Bridge API to get a LiveKit token.

    Args:
        participant_name: Display name for the participant.
        api_key: Vocal Bridge API key for the target agent.
        room_code: If provided, request a token for this existing VB room
                   so multiple participants end up in the same LiveKit room.
    """
    payload: dict = {"participant_name": participant_name}
    if room_code:
        payload["room_code"] = room_code

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            VOCAL_BRIDGE_TOKEN_URL,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/token", response_model=MeetTokenResponse)
async def generate_meet_token(body: MeetTokenRequest) -> MeetTokenResponse:
    """Generate a LiveKit token for a participant to join a room.

    First participant:  creates a new VB session and stores the room info.
    Subsequent participants:  request a token for the SAME LiveKit room.
    """
    room_code = body.room_name.strip().lower()

    # Look up the requested agent from the registry.
    registry = settings.agent_registry
    agent = registry.get(body.agent_id)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{body.agent_id}' not found. Available: {list(registry.keys())}",
        )

    try:
        existing = _rooms.get(room_code)

        if existing:
            # ── Existing room: pass the stored VB room name so the
            # participant joins the SAME LiveKit room.
            vb_room = existing["livekit_room"]
            logger.info(
                "Room '%s' exists (lk_room=%s), issuing token for existing room.",
                room_code, vb_room,
            )
            data = await _request_vb_token(
                body.participant_name, api_key=agent.api_key, room_code=vb_room,
            )
            existing["participants"] += 1

            # Return the STORED livekit_url and room_name (not the new
            # response, which might differ if VB doesn't honour room_code).
            return MeetTokenResponse(
                livekit_url=existing["livekit_url"],
                token=data.get("token", ""),
                room_name=existing["livekit_room"],
                participant_identity=data.get("participant_identity", body.participant_name),
            )

        else:
            # ── New room: first participant.
            logger.info("Creating new room '%s' with agent '%s'.", room_code, body.agent_id)
            data = await _request_vb_token(body.participant_name, api_key=agent.api_key)

            _rooms[room_code] = {
                "livekit_url": data.get("livekit_url", ""),
                "livekit_room": data.get("room_name", ""),
                "participants": 1,
                "created_at": time.time(),
            }

            return MeetTokenResponse(
                livekit_url=data.get("livekit_url", ""),
                token=data.get("token", ""),
                room_name=data.get("room_name", room_code),
                participant_identity=data.get("participant_identity", body.participant_name),
            )

    except httpx.HTTPStatusError as exc:
        logger.error("Vocal Bridge API %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Vocal Bridge API error: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        logger.exception("Failed to reach Vocal Bridge API")
        raise HTTPException(status_code=502, detail=f"Could not reach Vocal Bridge API: {exc}")


@router.post("/handoff", response_model=HandoffResponse)
async def handoff_to_specialist(body: HandoffRequest) -> HandoffResponse:
    """Generate a token for a specialist agent.

    The client disconnects from the current room and connects to the
    specialist's room.
    """
    registry = settings.agent_registry
    agent = registry.get(body.agent_id)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{body.agent_id}' not found. Available: {list(registry.keys())}",
        )

    try:
        data = await _request_vb_token(body.participant_name, api_key=agent.api_key)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Vocal Bridge error: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return HandoffResponse(
        livekit_url=data.get("livekit_url", ""),
        token=data.get("token", ""),
        room_name=data.get("room_name", ""),
        participant_identity=data.get("participant_identity", body.participant_name),
        agent_id=body.agent_id,
        agent_name=agent.name,
    )


@router.get("/agents")
async def list_agents():
    """List available agents and their roles."""
    registry = settings.agent_registry
    return {
        "agents": [
            {"id": k, "name": v.name, "role": v.role, "description": v.description}
            for k, v in registry.items()
        ]
    }


@router.get("/rooms", response_model=ActiveRoomsResponse)
async def list_rooms() -> ActiveRoomsResponse:
    """List active meeting rooms."""
    rooms = []
    for code, info in _rooms.items():
        rooms.append(RoomInfoResponse(
            room_code=code,
            livekit_room=info.get("livekit_room"),
            participants=info.get("participants", 0),
            created_at=info.get("created_at", 0),
            invite_link=f"/meet.html?room={code}&name=Guest",
        ))
    return ActiveRoomsResponse(rooms=rooms)


@router.delete("/rooms/{room_code}")
async def delete_room(room_code: str):
    """Remove a room from the registry."""
    if room_code in _rooms:
        del _rooms[room_code]
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Room not found.")


@router.get("/status", response_model=MeetStatusResponse)
async def meet_status() -> MeetStatusResponse:
    """Check whether the meeting service is available."""
    return MeetStatusResponse(available=True, agent_name=AGENT_NAME)

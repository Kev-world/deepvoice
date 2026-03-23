"""FastAPI routes for the Google Meet audio bridge."""

import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

from src.bridge.audio_bridge import AudioBridge
from src.bridge.meet_bot import GoogleMeetBot
from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bridge")

# Singleton bridge instance (one meeting at a time)
_bridge: AudioBridge | None = None
_bridge_task: asyncio.Task | None = None

# Singleton bot instance
_bot: GoogleMeetBot | None = None
_bot_task: asyncio.Task | None = None

VOCAL_BRIDGE_TOKEN_URL = "https://vocalbridgeai.com/api/v1/token"


class StartBridgeRequest(BaseModel):
    """Request to start the audio bridge."""
    meeting_url: str
    participant_name: str = "DeepVoice AI"
    room_code: str | None = None  # Optional: join existing LiveKit room by code


class BridgeStatusResponse(BaseModel):
    """Status of the audio bridge."""
    active: bool
    meeting_url: str | None = None
    livekit_room: str | None = None


async def _get_livekit_token(participant_name: str, room_code: str | None = None) -> dict:
    """Get a LiveKit token from Vocal Bridge API."""
    payload = {"participant_name": participant_name}
    if room_code:
        payload["room_code"] = room_code

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            VOCAL_BRIDGE_TOKEN_URL,
            headers={
                "X-API-Key": settings.vocal_bridge_api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return response.json()


@router.post("/start", response_model=BridgeStatusResponse)
async def start_bridge(body: StartBridgeRequest):
    """Start the audio bridge - connects to LiveKit room.

    After calling this, the browser bot should connect to the WebSocket
    endpoint at /api/bridge/ws to begin streaming audio.
    """
    global _bridge

    if _bridge is not None:
        raise HTTPException(status_code=409, detail="A bridge is already active. Stop it first.")

    try:
        # Get LiveKit token
        token_data = await _get_livekit_token(body.participant_name, body.room_code)

        # Create and connect bridge
        bridge = AudioBridge()
        await bridge.connect_livekit(
            livekit_url=token_data["livekit_url"],
            token=token_data["token"],
        )

        _bridge = bridge

        return BridgeStatusResponse(
            active=True,
            meeting_url=body.meeting_url,
            livekit_room=token_data.get("room_name"),
        )
    except httpx.HTTPError as exc:
        logger.exception("Failed to get LiveKit token")
        raise HTTPException(status_code=502, detail=f"Failed to get LiveKit token: {exc}")
    except Exception as exc:
        logger.exception("Failed to start audio bridge")
        raise HTTPException(status_code=500, detail=f"Failed to start bridge: {exc}")


@router.post("/stop")
async def stop_bridge():
    """Stop the active audio bridge."""
    global _bridge

    if _bridge is None:
        return {"status": "no bridge active"}

    await _bridge.disconnect()
    _bridge = None
    return {"status": "stopped"}


@router.get("/status", response_model=BridgeStatusResponse)
async def bridge_status():
    """Get the current bridge status."""
    if _bridge is None:
        return BridgeStatusResponse(active=False)
    return BridgeStatusResponse(active=True)


class StartBotRequest(BaseModel):
    """Request to start the Playwright bot."""
    meeting_url: str
    bot_name: str = "DeepVoice AI"


async def _run_bot(meeting_url: str, bot_name: str, ws_url: str) -> None:
    """Run the Meet bot in the background."""
    global _bot
    bot = GoogleMeetBot(meeting_url=meeting_url, bot_name=bot_name, ws_url=ws_url)
    _bot = bot
    try:
        await bot.run()
    except Exception:
        logger.exception("Meet bot crashed")
    finally:
        _bot = None


@router.post("/bot/start")
async def start_bot(body: StartBotRequest):
    """Launch the Playwright browser bot to join Google Meet."""
    global _bot_task

    if _bot is not None:
        raise HTTPException(status_code=409, detail="Bot is already running.")

    ws_url = "ws://localhost:8000/api/bridge/ws"
    _bot_task = asyncio.create_task(_run_bot(body.meeting_url, body.bot_name, ws_url))
    return {"status": "launched", "meeting_url": body.meeting_url}


@router.post("/bot/stop")
async def stop_bot():
    """Stop the running Playwright bot."""
    global _bot, _bot_task

    if _bot is not None:
        await _bot.leave_meeting()
        _bot = None

    if _bot_task is not None:
        _bot_task.cancel()
        _bot_task = None

    return {"status": "stopped"}


@router.websocket("/ws")
async def audio_bridge_websocket(ws: WebSocket):
    """WebSocket endpoint for the Chrome tab audio bridge.

    The browser bot connects here to stream Google Meet audio
    and receive Vocal Bridge agent audio.
    """
    global _bridge

    if _bridge is None:
        await ws.close(code=4000, reason="No active bridge. Call /api/bridge/start first.")
        return

    await _bridge.handle_websocket(ws)

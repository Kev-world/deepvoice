"""WebSocket audio bridge between Google Meet (Chrome) and LiveKit room."""

import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from livekit import rtc

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
NUM_CHANNELS = 1
SAMPLES_PER_CHANNEL = 4096  # Match the browser's buffer size


class AudioBridge:
    """Bridges audio between a WebSocket (Chrome tab) and a LiveKit room.

    Flow:
        Chrome (Google Meet) -> WebSocket -> AudioBridge -> LiveKit room -> Vocal Bridge Agent
        Vocal Bridge Agent -> LiveKit room -> AudioBridge -> WebSocket -> Chrome (Google Meet)
    """

    def __init__(self) -> None:
        self._room: Optional[rtc.Room] = None
        self._audio_source: Optional[rtc.AudioSource] = None
        self._ws: Optional[WebSocket] = None
        self._connected = False

    async def connect_livekit(self, livekit_url: str, token: str) -> None:
        """Connect to the LiveKit room where the Vocal Bridge agent lives.

        Args:
            livekit_url: The LiveKit server WebSocket URL.
            token: A LiveKit access token for this participant.
        """
        self._room = rtc.Room()

        # Set up event handlers
        self._room.on("track_subscribed", self._on_track_subscribed)
        self._room.on("disconnected", self._on_disconnected)

        logger.info("Connecting to LiveKit room at %s", livekit_url)
        await self._room.connect(livekit_url, token)
        self._connected = True
        logger.info("Connected to LiveKit room: %s", self._room.name)

        # Create an audio source to publish meeting audio into the room
        self._audio_source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)

        # Publish the audio track
        track = rtc.LocalAudioTrack.create_audio_track("meet-audio", self._audio_source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(track, options)
        logger.info("Published meet-audio track to LiveKit room.")

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Handle incoming audio from the Vocal Bridge agent."""
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return

        logger.info(
            "Subscribed to audio track from participant: %s",
            participant.identity,
        )

        # Stream agent audio back to the Chrome tab via WebSocket
        audio_stream = rtc.AudioStream(track)
        asyncio.create_task(self._forward_agent_audio(audio_stream))

    async def _forward_agent_audio(self, audio_stream: rtc.AudioStream) -> None:
        """Forward audio from the LiveKit agent to the Chrome WebSocket."""
        async for frame_event in audio_stream:
            if self._ws is None:
                continue

            try:
                frame = frame_event.frame
                # Convert the audio frame to Int16 PCM bytes
                int16_data = frame.data.tobytes()
                await self._ws.send_bytes(int16_data)
            except Exception:
                logger.debug("Failed to forward agent audio frame.")

    def _on_disconnected(self) -> None:
        """Handle LiveKit disconnection."""
        logger.warning("Disconnected from LiveKit room.")
        self._connected = False

    async def handle_websocket(self, ws: WebSocket) -> None:
        """Handle a WebSocket connection from the Chrome tab.

        Google Meet navigates/reloads several times, so the init script
        will open multiple WebSocket connections.  Each new connection
        silently replaces the previous one.
        """
        await ws.accept()

        # Replace the previous socket (the old one is already dead).
        old_ws = self._ws
        self._ws = ws
        logger.info("Chrome audio bridge WebSocket connected.")

        try:
            while True:
                message = await ws.receive()

                # Starlette puts a "type" key of "websocket.disconnect"
                # when the client closes the connection.
                if message.get("type") == "websocket.disconnect":
                    break

                if "text" in message:
                    msg = json.loads(message["text"])
                    if msg.get("type") == "hello":
                        logger.info("Chrome bridge hello: sample_rate=%s", msg.get("sampleRate"))
                        await ws.send_json({"type": "ready"})

                elif "bytes" in message:
                    await self._forward_meet_audio(message["bytes"])

        except (WebSocketDisconnect, RuntimeError):
            # RuntimeError is raised by starlette if we call receive()
            # after the client has already disconnected.
            pass
        except Exception:
            logger.exception("Error in WebSocket audio bridge.")
        finally:
            # Only clear _ws if it still points to *this* socket
            # (a newer connection may have already replaced it).
            if self._ws is ws:
                self._ws = None
            logger.info("Chrome audio bridge WebSocket closed.")

    async def _forward_meet_audio(self, pcm_bytes: bytes) -> None:
        """Forward audio from Google Meet to the LiveKit room.

        Args:
            pcm_bytes: Raw Int16 PCM audio data.
        """
        if self._audio_source is None:
            return

        try:
            # Create an AudioFrame from the raw PCM data
            # livekit expects Int16 samples
            num_samples = len(pcm_bytes) // 2  # Int16 = 2 bytes per sample

            frame = rtc.AudioFrame.create(SAMPLE_RATE, NUM_CHANNELS, num_samples)
            frame_data = frame.data

            # Copy PCM bytes into the frame
            src = memoryview(pcm_bytes)
            dst = memoryview(frame_data).cast('B')
            dst[:len(pcm_bytes)] = src

            await self._audio_source.capture_frame(frame)
        except Exception:
            logger.debug("Failed to forward Meet audio to LiveKit.")

    async def disconnect(self) -> None:
        """Disconnect from LiveKit and close WebSocket."""
        if self._room:
            await self._room.disconnect()
            self._room = None
        self._connected = False
        logger.info("Audio bridge disconnected.")

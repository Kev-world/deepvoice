#!/usr/bin/env python3
"""Join a Google Meet call with the DeepVoice AI agent.

Usage:
    python scripts/join_meet.py <meeting_url> [--name "DeepVoice AI"] [--room-code <code>] [--ws-port 8000]

Examples:
    python scripts/join_meet.py "https://meet.google.com/abc-defg-hij"
    python scripts/join_meet.py "https://meet.google.com/abc-defg-hij" --name "AI Assistant"
    python scripts/join_meet.py "https://meet.google.com/abc-defg-hij" --room-code "my-room"

The script:
1. Starts the audio bridge (connects to LiveKit via Vocal Bridge API)
2. Launches a Chrome browser via Playwright
3. Injects audio capture/playback scripts BEFORE navigating to Google Meet
4. Joins the Google Meet call
5. Bridges audio bidirectionally between Meet and the Vocal Bridge agent
6. Exits when the meeting ends or Ctrl+C is pressed

Prerequisites:
    - The FastAPI server must be running: uv run python -m src.main
    - Playwright browsers must be installed: uv run playwright install chromium
    - A valid VOCAL_BRIDGE_API_KEY must be set in .env
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bridge.meet_bot import GoogleMeetBot

logger = logging.getLogger(__name__)


async def start_bridge(base_url: str, meeting_url: str, name: str, room_code: str | None) -> dict:
    """Call the /api/bridge/start endpoint to initialize the LiveKit connection."""
    payload = {"meeting_url": meeting_url, "participant_name": name}
    if room_code:
        payload["room_code"] = room_code

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{base_url}/api/bridge/start", json=payload)
        resp.raise_for_status()
        return resp.json()


async def stop_bridge(base_url: str) -> None:
    """Call the /api/bridge/stop endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{base_url}/api/bridge/stop")
    except Exception:
        logger.debug("Failed to stop bridge (server may be down).")


async def check_server(base_url: str) -> bool:
    """Check if the FastAPI server is running."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/health")
            return resp.status_code == 200
    except Exception:
        return False


async def main(args: argparse.Namespace) -> None:
    base_url = f"http://localhost:{args.ws_port}"
    ws_url = f"ws://localhost:{args.ws_port}/api/bridge/ws"

    # Check server is running
    print("Checking if DeepVoice server is running...")
    if not await check_server(base_url):
        print(f"ERROR: DeepVoice server is not running at {base_url}")
        print("Start it first: uv run python -m src.main")
        sys.exit(1)
    print("Server is running.")

    # Start the audio bridge (LiveKit connection)
    print("Starting audio bridge...")
    try:
        bridge_info = await start_bridge(base_url, args.meeting_url, args.name, args.room_code)
        print(f"Audio bridge started. LiveKit room: {bridge_info.get('livekit_room', 'N/A')}")
    except httpx.HTTPStatusError as e:
        print(f"ERROR: Failed to start bridge: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to start bridge: {e}")
        sys.exit(1)

    # Launch the Meet bot
    print(f"Launching browser to join: {args.meeting_url}")
    print(f"Bot name: {args.name}")

    bot = GoogleMeetBot(
        meeting_url=args.meeting_url,
        bot_name=args.name,
        ws_url=ws_url,
    )

    # Handle Ctrl+C gracefully
    shutdown_event = asyncio.Event()

    def signal_handler():
        print("\nShutting down...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await bot.launch()

        # Inject scripts BEFORE joining - this is critical for intercepting getUserMedia
        # We inject early, then navigate to Meet
        await bot.inject_audio_bridge()

        await bot.join_meeting()

        print("=" * 60)
        print("DeepVoice AI agent is now in the meeting!")
        print("The agent will listen and respond to questions.")
        print("Press Ctrl+C to leave the meeting.")
        print("=" * 60)

        # Wait for meeting to end or shutdown signal
        meeting_task = asyncio.create_task(bot.wait_until_meeting_ends())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [meeting_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        print("Leaving meeting...")
        await bot.leave_meeting()
        await stop_bridge(base_url)
        print("Done.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join a Google Meet call with the DeepVoice AI agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/join_meet.py "https://meet.google.com/abc-defg-hij"
  python scripts/join_meet.py "https://meet.google.com/abc-defg-hij" --name "AI Bot"
  python scripts/join_meet.py "https://meet.google.com/abc-defg-hij" --room-code "my-room"
        """,
    )
    parser.add_argument("meeting_url", help="Google Meet URL to join")
    parser.add_argument("--name", default="DeepVoice AI", help="Bot display name (default: DeepVoice AI)")
    parser.add_argument("--room-code", default=None, help="LiveKit room code to join (optional)")
    parser.add_argument("--ws-port", type=int, default=8000, help="DeepVoice server port (default: 8000)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    asyncio.run(main(args))

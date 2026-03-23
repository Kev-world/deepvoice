"""LiveKit room connection manager using the Vocal Bridge API."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VOCAL_BRIDGE_TOKEN_URL = "https://vocalbridgeai.com/api/v1/token"

# Timeout configuration for Vocal Bridge API calls (seconds).
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


class RoomManager:
    """Manages LiveKit room connections via the Vocal Bridge API.

    All network calls use ``httpx.AsyncClient`` and are fully asynchronous.
    """

    def __init__(self, timeout: httpx.Timeout | None = None) -> None:
        self._timeout = timeout or _DEFAULT_TIMEOUT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_room_token(
        self,
        participant_name: str,
        api_key: str,
    ) -> dict[str, Any]:
        """Request a new LiveKit room token from the Vocal Bridge API.

        Args:
            participant_name: Display name for the participant joining the room.
            api_key: Vocal Bridge API key used for authentication.

        Returns:
            A dict containing at minimum:
                - livekit_url: The LiveKit server URL to connect to.
                - token: The signed JWT token for authentication.
                - room_name: The name of the LiveKit room.
                - participant_identity: The identity assigned to the participant.
                - expires_in: Token lifetime in seconds.

        Raises:
            httpx.HTTPStatusError: If the Vocal Bridge API returns an error status.
            httpx.RequestError: If the request fails due to a network issue.
        """
        logger.info(
            "Requesting room token for participant '%s'.", participant_name
        )

        payload: dict[str, Any] = {
            "participant_name": participant_name,
        }

        data = await self._post_token_request(payload, api_key)

        logger.info(
            "Token received for room '%s' (participant: '%s', expires in %ss).",
            data.get("room_name"),
            data.get("participant_identity"),
            data.get("expires_in"),
        )
        return data

    async def join_room_by_code(
        self,
        room_code: str,
        api_key: str,
    ) -> dict[str, Any]:
        """Join an existing LiveKit room using a room code.

        Args:
            room_code: The short code identifying the room to join.
            api_key: Vocal Bridge API key used for authentication.

        Returns:
            A dict with the same shape as ``create_room_token``.

        Raises:
            httpx.HTTPStatusError: If the Vocal Bridge API returns an error status.
            httpx.RequestError: If the request fails due to a network issue.
        """
        logger.info("Joining room by code '%s'.", room_code)

        payload: dict[str, Any] = {
            "room_code": room_code,
        }

        data = await self._post_token_request(payload, api_key)

        logger.info(
            "Joined room '%s' via code '%s'.",
            data.get("room_name"),
            room_code,
        )
        return data

    @staticmethod
    def get_connection_info(token_response: dict[str, Any]) -> dict[str, Any]:
        """Extract and format connection information from a token response.

        This is a convenience method that normalises the Vocal Bridge response
        into the minimal set of fields a client needs to establish a WebRTC
        connection through LiveKit.

        Args:
            token_response: The raw dict returned by ``create_room_token`` or
                ``join_room_by_code``.

        Returns:
            A dict with keys: ``livekit_url``, ``token``, ``room_name``,
            ``participant_identity``, and ``expires_in``.
        """
        return {
            "livekit_url": token_response.get("livekit_url"),
            "token": token_response.get("token"),
            "room_name": token_response.get("room_name"),
            "participant_identity": token_response.get("participant_identity"),
            "expires_in": token_response.get("expires_in"),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_token_request(
        self,
        payload: dict[str, Any],
        api_key: str,
    ) -> dict[str, Any]:
        """Send a POST request to the Vocal Bridge token endpoint.

        Args:
            payload: JSON body to send.
            api_key: API key placed in the ``Authorization`` header.

        Returns:
            The parsed JSON response as a dict.
        """
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    VOCAL_BRIDGE_TOKEN_URL,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return data
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Vocal Bridge API returned HTTP %d: %s",
                    exc.response.status_code,
                    exc.response.text,
                )
                raise
            except httpx.RequestError as exc:
                logger.error(
                    "Network error contacting Vocal Bridge API: %s", exc
                )
                raise

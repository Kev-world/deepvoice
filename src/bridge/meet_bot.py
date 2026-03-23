import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page

logger = logging.getLogger(__name__)

MEET_SCRIPTS_PATH = Path(__file__).parent / "meet_scripts.js"
# Persistent profile so Google sign-in survives across runs.
PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "chrome-profile"


class GoogleMeetBot:
    """Joins a Google Meet call via Playwright and bridges audio to/from a WebSocket."""

    def __init__(
        self,
        meeting_url: str,
        bot_name: str = "DeepVoice AI",
        ws_url: str = "ws://localhost:8000/api/bridge/ws",
    ):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.ws_url = ws_url
        self._pw = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def launch(self) -> None:
        """Launch system Chrome with a persistent profile directory."""
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        # launch_persistent_context uses a real user data dir, so
        # Google sign-in, cookies, and preferences all persist.
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
            permissions=["microphone", "camera"],
            accept_downloads=False,
        )
        # Use the default page that persistent context opens, or create one.
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        logger.info("Browser launched with persistent profile at %s", PROFILE_DIR)

    async def join_meeting(self) -> None:
        """Navigate to Meet URL and click through the join flow."""
        if not self._page:
            raise RuntimeError("Browser not launched. Call launch() first.")

        page = self._page
        logger.info("Navigating to Google Meet: %s", self.meeting_url)

        await page.goto(self.meeting_url, wait_until="load", timeout=60000)

        # Log current state so we can debug sign-in / pre-join issues.
        await asyncio.sleep(5)
        current_url = page.url
        title = await page.title()
        logger.info("Page loaded — url=%s  title=%s", current_url, title)

        # If redirected to sign-in, wait for the user to complete it.
        if "accounts.google.com" in current_url:
            logger.warning(
                "Google sign-in required. Please sign in to Google in the "
                "browser window. Waiting up to 120 seconds..."
            )
            try:
                await page.wait_for_url("**/meet.google.com/**", timeout=120000)
                logger.info("Sign-in complete, now on Meet.")
                await asyncio.sleep(3)
            except Exception:
                logger.error("Timed out waiting for Google sign-in.")
                return

        # Dismiss "Got it" or cookie consent if present.
        for dismiss_text in ["Got it", "Accept all", "Dismiss"]:
            try:
                btn = page.locator(f'button:has-text("{dismiss_text}")')
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        # Turn off camera.
        try:
            camera_btn = page.locator(
                '[data-is-muted="false"][aria-label*="camera" i]'
            )
            if await camera_btn.count() > 0:
                await camera_btn.first.click()
                await asyncio.sleep(0.5)
        except Exception:
            logger.debug("Could not toggle camera off.")

        # Turn off microphone.
        try:
            mic_btn = page.locator(
                '[data-is-muted="false"][aria-label*="microphone" i]'
            )
            if await mic_btn.count() > 0:
                await mic_btn.first.click()
                await asyncio.sleep(0.5)
        except Exception:
            logger.debug("Could not toggle microphone off.")

        # Enter bot name if there's a name input.
        try:
            name_input = page.locator('input[aria-label="Your name"]')
            if await name_input.count() > 0:
                await name_input.fill(self.bot_name)
                await asyncio.sleep(0.5)
        except Exception:
            logger.debug("No name input found.")

        # Click the join button.
        join_clicked = False
        for selector in [
            'button:has-text("Ask to join")',
            'button:has-text("Join now")',
            'button:has-text("Join")',
            '[jsname="Qx7uuf"]',
            '[data-idom-class*="join"] button',
            'button[data-mdc-dialog-action="join"]',
            '[role="button"]:has-text("Join")',
            '[role="button"]:has-text("Ask to join")',
        ]:
            try:
                btn = page.locator(selector)
                if await btn.count() > 0:
                    await btn.first.click(timeout=3000)
                    join_clicked = True
                    logger.info("Clicked join button: %s", selector)
                    break
            except Exception:
                continue

        if not join_clicked:
            logger.warning("Could not find join button, pressing Enter as fallback.")
            await page.keyboard.press("Enter")

        await asyncio.sleep(5)
        logger.info("Joined meeting (or waiting for admission). url=%s", page.url)

    async def inject_audio_bridge(self) -> None:
        """Register the audio bridge script to run before any page JS."""
        if not self._context:
            raise RuntimeError("Browser not launched.")

        js_code = MEET_SCRIPTS_PATH.read_text()

        await self._context.add_init_script(
            f"window.__DEEPVOICE_WS_URL = '{self.ws_url}';\n{js_code}"
        )
        logger.info("Audio bridge init script registered.")

    async def wait_until_meeting_ends(self) -> None:
        """Block until the meeting ends or the bot is removed."""
        if not self._page:
            return

        GOOGLE_DOMAINS = ("meet.google.com", "accounts.google.com", "google.com")

        logger.info("Monitoring meeting status...")
        while True:
            try:
                url = self._page.url

                # Check if we navigated completely away from Google.
                if not any(d in url for d in GOOGLE_DOMAINS):
                    logger.info("Left all Google domains (url=%s).", url)
                    break

                # Check for end-of-meeting indicators on the page.
                for text in [
                    "You left the meeting",
                    "You've been removed from the meeting",
                    "The meeting has ended",
                ]:
                    indicator = self._page.locator(f'text="{text}"')
                    if await indicator.count() > 0:
                        logger.info("Meeting ended: '%s' detected.", text)
                        return

                await asyncio.sleep(5)
            except Exception:
                logger.debug("Error checking meeting status, continuing...")
                await asyncio.sleep(5)

    async def leave_meeting(self) -> None:
        """Leave the meeting and close the browser."""
        if self._page:
            try:
                leave_btn = self._page.locator('[aria-label="Leave call"]')
                if await leave_btn.count() > 0:
                    await leave_btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        if self._context:
            await self._context.close()
        if self._pw:
            await self._pw.stop()
        logger.info("Browser closed.")

    async def run(self) -> None:
        """Full lifecycle: launch -> inject -> join -> wait -> leave."""
        try:
            await self.launch()
            await self.inject_audio_bridge()
            await self.join_meeting()
            await self.wait_until_meeting_ends()
        except Exception:
            logger.exception("Error during meeting bot lifecycle.")
        finally:
            await self.leave_meeting()

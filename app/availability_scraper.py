"""
Web scraper for checking badminton court availability using Playwright.
Scrapes the visual availability table to determine which slots are free.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from playwright.async_api import Browser, Page, Playwright, async_playwright

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class AvailabilityScraper:
    """
    Scrapes court availability from the booking website.
    Green slots = available, Red slots = booked.
    """

    BASE_URL = "https://klient.zatokasportu.pl"
    COURT_IDS = [34623, 34624, 34625, 34626]  # IDs for courts 1-4

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        """Context manager entry - initialize browser"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup browser"""
        await self.close()

    async def start(self):
        """Start the browser instance"""
        if not self.browser:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )

    async def close(self):
        """Close the browser instance"""
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    async def get_available_slots(
        self,
        date: datetime,
        start_time: str = "06:30",
        end_time: str = "21:30",
        cookies: Optional[List[Dict]] = None,
    ) -> Dict[str, List[int]]:
        """
        Get all available slots for a specific date within a time range.

        Args:
            date: The date to check
            start_time: Start of time window (HH:MM format)
            end_time: End of time window (HH:MM format)
            cookies: Optional list of cookies for authentication

        Returns:
            Dict mapping time slots to list of available court numbers.
            Example: {"06:30": [1, 2, 3], "07:30": [1, 3], ...}
        """
        async with self._lock:
            if not self.browser:
                await self.start()

            page = await self.browser.new_page()

            # Set cookies if provided (for authentication)
            if cookies:
                await page.context.add_cookies(cookies)

            try:
                # Navigate to the booking page for the specific date
                date_str = date.strftime("%Y-%m-%d")
                url = f"{self.BASE_URL}/index.php?s=badminton&date={date_str}"

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Wait a bit for JavaScript to load and modals to appear
                await page.wait_for_timeout(3000)

                # Close any modal dialogs/alerts
                try:
                    # Close visible alerts
                    visible_alerts = await page.query_selector_all(
                        ".alert:not(.hidden)"
                    )
                    for alert in visible_alerts:
                        try:
                            close_btn = await alert.query_selector(
                                '.close, button[data-dismiss="alert"]'
                            )
                            if close_btn:
                                await close_btn.click()
                                await page.wait_for_timeout(500)
                        except:
                            pass

                    # Close modal if visible
                    modal_visible = await page.query_selector(".modal.show")
                    if modal_visible:
                        close_button_selectors = [
                            ".modal.show .close",
                            '.modal.show button[data-dismiss="modal"]',
                            ".modal.show .modal-header .close",
                            ".modal.show button.close",
                        ]
                        for selector in close_button_selectors:
                            try:
                                close_btn = await page.query_selector(selector)
                                if close_btn:
                                    await close_btn.click()
                                    await page.wait_for_timeout(1000)
                                    break
                            except:
                                continue
                except Exception as e:
                    logger.warning(f"Error closing modals/alerts: {e}")

                # Verify we're on the correct page
                try:
                    court_ids = await page.evaluate(
                        """
                        () => {
                            const ids = Array.from(document.querySelectorAll('[data-link*="id="]'))
                                .map(el => {
                                    const match = el.getAttribute('data-link').match(/id=(\\d+)/);
                                    return match ? parseInt(match[1]) : null;
                                })
                                .filter(id => id !== null);
                            return [...new Set(ids)].sort();
                        }
                    """
                    )
                    expected_badminton_ids = [34623, 34624, 34625, 34626]
                    if court_ids and not any(
                        cid in expected_badminton_ids for cid in court_ids
                    ):
                        logger.error(
                            f"Wrong sport detected - expected badminton courts {expected_badminton_ids}, found {court_ids}"
                        )
                        return {}
                except:
                    pass

                # Extract availability data from the page
                extracted_slots = await page.evaluate(
                    """
                    () => {
                        const slots = {};

                        // Helper function to convert minutes from midnight to HH:MM format
                        function minutesToTime(minutes) {
                            const hours = Math.floor(minutes / 60);
                            const mins = minutes % 60;
                            return `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}`;
                        }

                        // Find all court columns (.i-table-events)
                        const courtColumns = document.querySelectorAll('.i-table-events');
                        if (courtColumns.length === 0) {
                            return slots;
                        }

                        // Process each court column
                        courtColumns.forEach((courtCol, courtIndex) => {
                            const courtNumber = courtIndex + 1;

                            // Find all available slots (green divs with ZAREZERWUJ text)
                            const availableSlots = courtCol.querySelectorAll('.i-table-event.click');

                            availableSlots.forEach((slot) => {
                                const bgColor = window.getComputedStyle(slot).backgroundColor;
                                const text = slot.textContent.trim();
                                const hasReserveText = text.includes('ZAREZERWUJ');
                                const dataStart = slot.getAttribute('data-start');

                                // Check if it's a green (available) slot
                                const isGreen = bgColor.includes('176, 80') || bgColor.includes('0, 176, 80') || bgColor.includes('#00B050');

                                if (dataStart && isGreen && hasReserveText) {
                                    const timeInMinutes = parseInt(dataStart);
                                    const timeStr = minutesToTime(timeInMinutes);

                                    if (!slots[timeStr]) {
                                        slots[timeStr] = [];
                                    }
                                    if (!slots[timeStr].includes(courtNumber)) {
                                        slots[timeStr].push(courtNumber);
                                    }
                                }
                            });
                        });

                        // Sort court numbers in each time slot
                        Object.keys(slots).forEach(time => {
                            slots[time].sort((a, b) => a - b);
                        });

                        return slots;
                    }
                """
                )

                logger.info(
                    f"Found {len(extracted_slots)} available time slots on {date_str}"
                )

                # Filter by time range if specified
                if start_time or end_time:
                    filtered_slots = {}
                    for time_slot, courts in extracted_slots.items():
                        if self._is_time_in_range(time_slot, start_time, end_time):
                            filtered_slots[time_slot] = courts
                    return filtered_slots

                return extracted_slots

            except Exception as e:
                logger.error(f"Scraping error: {str(e)}")
                return {}
            finally:
                await page.close()

    async def is_slot_available(
        self,
        date: datetime,
        time: str,
        court_number: int,
        cookies: Optional[List[Dict]] = None,
    ) -> bool:
        """
        Check if a specific time slot is available for a specific court.

        Args:
            date: The date to check
            time: Time in HH:MM format
            court_number: Court number (1-4)
            cookies: Optional list of cookies for authentication

        Returns:
            True if the slot is available, False otherwise
        """
        available_slots = await self.get_available_slots(date, time, time, cookies)
        return court_number in available_slots.get(time, [])

    async def find_continuous_slots(
        self,
        date: datetime,
        start_time: str,
        hours: int,
        num_courts: int = 1,
        end_time: Optional[str] = None,
        cookies: Optional[List[Dict]] = None,
    ) -> List[Tuple[str, List[int]]]:
        """
        Find continuous available slots for the requested duration.

        Args:
            date: The date to check
            start_time: Earliest start time to consider
            hours: Number of continuous hours needed
            num_courts: Number of courts needed simultaneously
            end_time: Latest start time to consider (optional)
            cookies: Optional list of cookies for authentication

        Returns:
            List of tuples (start_time, [court_numbers]) for each continuous hour.
            Empty list if no continuous slots found.
        """
        # Get all available slots for the day
        available_slots = await self.get_available_slots(
            date, start_time, end_time, cookies
        )

        if not available_slots:
            return []

        # Generate time slots needed for continuous hours
        start_dt = datetime.strptime(start_time, "%H:%M")
        required_times = []
        for hour in range(hours):
            time_dt = start_dt + timedelta(hours=hour)
            required_times.append(time_dt.strftime("%H:%M"))

        # Find courts available for all required times
        continuous_courts = None
        for time_slot in required_times:
            if time_slot not in available_slots:
                return []

            courts = set(available_slots[time_slot])

            if continuous_courts is None:
                continuous_courts = courts
            else:
                continuous_courts = continuous_courts.intersection(courts)

            # If no courts remain available for all times, fail early
            if len(continuous_courts) < num_courts:
                return []

        # Convert to list and select requested number of courts
        available_court_list = sorted(list(continuous_courts))[:num_courts]

        # Build result: list of (time, courts) for each hour
        result = []
        for time_slot in required_times:
            result.append((time_slot, available_court_list))

        logger.info(
            f"Found {len(result)} continuous slots for {num_courts} court(s) starting at {start_time}"
        )
        return result

    def _is_time_in_range(
        self, time: str, start_time: Optional[str], end_time: Optional[str]
    ) -> bool:
        """Check if a time is within the specified range"""
        if not start_time and not end_time:
            return True

        time_minutes = self._time_to_minutes(time)

        if start_time:
            start_minutes = self._time_to_minutes(start_time)
            if time_minutes < start_minutes:
                return False

        if end_time:
            end_minutes = self._time_to_minutes(end_time)
            if time_minutes > end_minutes:
                return False

        return True

    @staticmethod
    def _time_to_minutes(time_str: str) -> int:
        """Convert HH:MM to minutes since midnight"""
        h, m = map(int, time_str.split(":"))
        return h * 60 + m


# Global scraper instance to reuse browser across requests
_global_scraper: Optional[AvailabilityScraper] = None


async def get_scraper() -> AvailabilityScraper:
    """Get or create the global scraper instance"""
    global _global_scraper
    if _global_scraper is None:
        _global_scraper = AvailabilityScraper()
        await _global_scraper.start()
    return _global_scraper


async def close_scraper():
    """Close the global scraper instance"""
    global _global_scraper
    if _global_scraper:
        await _global_scraper.close()
        _global_scraper = None

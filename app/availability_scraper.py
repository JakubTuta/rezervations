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
    Court IDs are dynamically extracted from the page for each date.
    """

    BASE_URL = "https://klient.zatokasportu.pl"

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
    ) -> Dict[str, Dict[int, int]]:
        """
        Get all available slots for a specific date within a time range.

        Args:
            date: The date to check
            start_time: Start of time window (HH:MM format)
            end_time: End of time window (HH:MM format)
            cookies: Optional list of cookies for authentication

        Returns:
            Dict mapping time slots to dict of {court_number: court_id}.
            Example: {"06:30": {1: 34403, 2: 34404}, "07:30": {1: 34403, 3: 34405}, ...}
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

                # Verify we're on the correct page by checking for court IDs
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
                    # Just verify we found some court IDs (they vary by date)
                    if not court_ids or len(court_ids) == 0:
                        logger.error(
                            f"No court IDs found on page - may be wrong sport or page didn't load correctly"
                        )
                        return {}
                    else:
                        logger.info(f"Found {len(court_ids)} courts with IDs: {court_ids}")
                except:
                    pass

                # Extract availability data from the page with court IDs
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

                            // Extract court ID from any slot in this column
                            let courtId = null;
                            const allSlots = courtCol.querySelectorAll('.i-table-event[data-link]');
                            for (const slot of allSlots) {
                                const dataLink = slot.getAttribute('data-link');
                                if (dataLink) {
                                    const match = dataLink.match(/id=(\d+)/);
                                    if (match) {
                                        courtId = parseInt(match[1]);
                                        break;
                                    }
                                }
                            }

                            // If no court ID found, skip this column
                            if (!courtId) return;

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
                                        slots[timeStr] = {};
                                    }
                                    // Store court number -> court ID mapping
                                    slots[timeStr][courtNumber] = courtId;
                                }
                            });
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
        return court_number in available_slots.get(time, {})

    async def find_continuous_slots(
        self,
        date: datetime,
        start_time: str,
        hours: int,
        num_courts: int = 1,
        end_time: Optional[str] = None,
        cookies: Optional[List[Dict]] = None,
    ) -> List[Tuple[str, Dict[int, int]]]:
        """
        Find continuous available slots for the requested duration.

        Note: Courts can be DIFFERENT for each hour. We just need num_courts available
        for each hour, not the same courts across all hours.

        Args:
            date: The date to check
            start_time: Earliest start time to consider
            hours: Number of continuous hours needed
            num_courts: Number of courts needed simultaneously per hour
            end_time: Latest start time to consider (optional)
            cookies: Optional list of cookies for authentication

        Returns:
            List of tuples (start_time, {court_number: court_id}) for each continuous hour.
            Courts may differ between hours.
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

        # For each hour, pick available courts (can be different courts per hour)
        result = []
        for time_slot in required_times:
            if time_slot not in available_slots:
                # This hour has no available slots at all
                logger.info(f"No courts available at {time_slot}")
                return []

            courts_at_time = available_slots[time_slot]  # {court_number: court_id}

            if len(courts_at_time) < num_courts:
                # Not enough courts available at this hour
                logger.info(
                    f"Only {len(courts_at_time)} court(s) available at {time_slot}, need {num_courts}"
                )
                return []

            # Pick the first num_courts available courts (sorted by court number)
            available_court_numbers = sorted(list(courts_at_time.keys()))[:num_courts]
            selected_courts = {
                court_num: courts_at_time[court_num]
                for court_num in available_court_numbers
            }

            result.append((time_slot, selected_courts))

        logger.info(
            f"Found {len(result)} continuous hours for {num_courts} court(s) starting at {start_time}"
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

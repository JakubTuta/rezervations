import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import requests

from app.availability_scraper import get_scraper


class ReservationService:
    """Multi-user reservation service"""

    BASE_URL = "https://klient.zatokasportu.pl"
    SERVICE_ID = "33676"
    COURT_IDS = [34623, 34624, 34625, 34626]
    MAX_RESERVATION_MINUTES = 21600  # 15 days

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session_dir = Path("data/sessions") / self._sanitize_email(email)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_file = self.session_dir / "cookies.pkl"

    @staticmethod
    def _sanitize_email(email: str) -> str:
        """Convert email to safe directory name"""
        return email.replace("@", "_at_").replace(".", "_")

    def login(self) -> bool:
        """Login and save session cookies"""
        login_url = f"{self.BASE_URL}/index.php?s=logowanie"
        login_data = {"email": self.email, "password": self.password, "login": "true"}

        try:
            response = self.session.post(login_url, data=login_data, timeout=10)
            if response.status_code == 200:
                self._save_cookies()
                return True
            else:
                return False
        except Exception as e:
            return False

    def _save_cookies(self):
        """Save session cookies to file"""
        with open(self.cookies_file, "wb") as f:
            pickle.dump(self.session.cookies, f)

    def _load_cookies(self) -> bool:
        """Load session cookies from file"""
        if self.cookies_file.exists():
            with open(self.cookies_file, "rb") as f:
                self.session.cookies.update(pickle.load(f))
            return True
        return False

    def is_session_valid(self) -> bool:
        """Check if current session is still valid"""
        try:
            test_url = f"{self.BASE_URL}/index.php?s=rezerwacja"
            response = self.session.get(test_url, timeout=10)
            return "logowanie" not in response.url and response.status_code == 200
        except:
            return False

    def ensure_authenticated(self) -> bool:
        """Ensure user is authenticated, login if needed"""
        if self._load_cookies() and self.is_session_valid():
            return True
        return self.login()

    def get_playwright_cookies(self) -> List[Dict]:
        """Convert requests session cookies to Playwright format"""
        playwright_cookies = []
        for cookie in self.session.cookies:
            playwright_cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain if cookie.domain else ".zatokasportu.pl",
                    "path": cookie.path if cookie.path else "/",
                    "expires": cookie.expires if cookie.expires else -1,
                    "httpOnly": (
                        cookie.has_nonstandard_attr("HttpOnly")
                        if hasattr(cookie, "has_nonstandard_attr")
                        else False
                    ),
                    "secure": cookie.secure if hasattr(cookie, "secure") else False,
                    "sameSite": "Lax",
                }
            )
        return playwright_cookies

    async def check_slot_availability(
        self, date: datetime, time: str, court_number: int
    ) -> bool:
        """
        Check if a specific slot is available using the web scraper.

        Args:
            date: The date to check
            time: Time in HH:MM format
            court_number: Court number (1-4)

        Returns:
            True if available, False if taken or error occurred
        """
        try:
            scraper = await get_scraper()
            cookies = self.get_playwright_cookies()
            return await scraper.is_slot_available(date, time, court_number, cookies)
        except Exception as e:
            # If scraper fails, return False (conservative approach)
            return False

    async def get_available_courts_for_time(
        self, date: datetime, time: str
    ) -> List[int]:
        """
        Get list of available court numbers for a specific date/time.

        Args:
            date: The date to check
            time: Time in HH:MM format

        Returns:
            List of available court numbers (1-4)
        """
        try:
            scraper = await get_scraper()
            cookies = self.get_playwright_cookies()
            available_slots = await scraper.get_available_slots(
                date, time, time, cookies
            )
            return available_slots.get(time, [])
        except Exception as e:
            # If scraper fails, return empty list
            return []

    def make_single_reservation(
        self, reservation_datetime: datetime, court_id: int
    ) -> Dict:
        """Make a single 1-hour reservation"""
        dt_start = reservation_datetime
        dt_end = dt_start + timedelta(hours=1)

        start_timestamp = int(dt_start.timestamp())
        end_timestamp = int(dt_end.timestamp())

        date = dt_start.strftime("%Y-%m-%d")
        time_slot = dt_start.strftime("%H:%M") + "|1|0.00|6"

        reservation_url = (
            f"{self.BASE_URL}/index.php?s=rezerwacja"
            f"&id={court_id}&start={start_timestamp}&end={end_timestamp}"
        )

        reservation_data = {
            "usluga": self.SERVICE_ID,
            f"godzina_{self.SERVICE_ID}": time_slot,
            f"ilosc_szt_godzina_{self.SERVICE_ID}": "1",
            "id": court_id,
            "data": date,
            "datat": start_timestamp,
            "rezerwacja": "1",
        }

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": reservation_url,
        }

        try:
            response = self.session.post(
                reservation_url, data=reservation_data, headers=headers, timeout=15
            )

            if response.status_code == 200:
                result = response.json()
                success = not result.get("error", True)

                # Extract reservation ID from successful booking
                reservation_id = None
                if success:
                    # Try to extract ID from response message or other fields
                    # The ID might be in the response somewhere
                    reservation_id = result.get("id") or result.get("reservation_id")

                return {
                    "success": success,
                    "message": result.get("msg", ""),
                    "datetime": dt_start,
                    "court_id": court_id,
                    "reservation_id": reservation_id,
                }
            else:
                return {
                    "success": False,
                    "message": f"HTTP {response.status_code}",
                    "datetime": dt_start,
                    "court_id": court_id,
                    "reservation_id": None,
                }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "datetime": dt_start,
                "court_id": court_id,
                "reservation_id": None,
            }

    async def make_continuous_reservations(
        self, start_datetime: datetime, hours: int, num_courts: int = 1
    ) -> List[Dict]:
        """
        Make continuous reservations for the specified hours.
        Uses scraper to check availability first, then books only confirmed available slots.
        Returns results only if we successfully book all requested hours.
        """
        if not self.ensure_authenticated():
            return [
                {
                    "success": False,
                    "message": "Authentication failed",
                    "datetime": start_datetime,
                    "court_id": None,
                    "court": None,
                }
            ]

        # Check availability for all required hours using the scraper
        try:
            scraper = await get_scraper()
            cookies = self.get_playwright_cookies()
            start_time_str = start_datetime.strftime("%H:%M")
            continuous_slots = await scraper.find_continuous_slots(
                date=start_datetime,
                start_time=start_time_str,
                hours=hours,
                num_courts=num_courts,
                cookies=cookies,
            )

            # If no continuous slots found, return error immediately
            if not continuous_slots:
                return [
                    {
                        "success": False,
                        "message": f"No {num_courts} continuous court(s) available for {hours} hours starting at {start_time_str}",
                        "datetime": start_datetime,
                        "court_id": None,
                        "court": None,
                    }
                ]
        except Exception as e:
            # If scraper fails, return error (no blind booking)
            return [
                {
                    "success": False,
                    "message": f"Unable to check availability: {str(e)}",
                    "datetime": start_datetime,
                    "court_id": None,
                    "court": None,
                }
            ]

        # Book the slots that scraper confirmed are available
        results = []

        for hour in range(hours):
            current_time = start_datetime + timedelta(hours=hour)
            time_slot, available_courts = continuous_slots[hour]

            # Book each court for this hour
            for court_number in available_courts:
                court_id = self.COURT_IDS[
                    court_number - 1
                ]  # Convert 1-indexed to 0-indexed
                result = self.make_single_reservation(current_time, court_id)
                result_with_meta = {
                    **result,
                    "court": court_number,
                    "hour_index": hour,
                }
                results.append(result_with_meta)

                # If booking failed for a confirmed available slot, something went wrong
                if not result["success"]:
                    return [
                        {
                            "success": False,
                            "message": f"Failed to book confirmed available slot at {time_slot} on court {court_number}: {result.get('message', 'Unknown error')}",
                            "datetime": current_time,
                            "court_id": court_id,
                            "court": court_number,
                        }
                    ]

        # All bookings successful
        return results

    async def find_slot_in_time_window(
        self,
        date: datetime,
        start_time_str: str,
        end_time_str: str,
        hours: int,
        num_courts: int = 1,
    ) -> List[Dict]:
        """
        Search for available slot within a time window on a specific date.
        Uses scraper to find available slots, then tries to book them.
        Tries every 30-minute interval from start_time to end_time.
        Returns empty list if no slot found.
        """
        # Parse start and end times
        start_h, start_m = map(int, start_time_str.split(":"))
        end_h, end_m = map(int, end_time_str.split(":"))

        # Create datetime objects for the search window
        search_start = date.replace(
            hour=start_h, minute=start_m, second=0, microsecond=0
        )
        search_end = date.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

        # Calculate the latest possible start time for the requested hours
        required_duration = timedelta(hours=hours)
        latest_start = search_end - required_duration

        # Try every 30-minute slot within the window
        current_slot = search_start
        while current_slot <= latest_start:
            results = await self.make_continuous_reservations(
                current_slot, hours, num_courts
            )

            # Check if all succeeded
            if results and all(r.get("success", False) for r in results):
                return results

            # Move to next 30-minute slot
            current_slot += timedelta(minutes=30)

        # No slot found in this time window
        return []

    @staticmethod
    def is_within_booking_window(target_datetime: datetime) -> bool:
        """Check if reservation is within 15-day booking window"""
        minutes_from_now = (target_datetime - datetime.now()).total_seconds() / 60
        return 0 <= minutes_from_now <= ReservationService.MAX_RESERVATION_MINUTES

    @staticmethod
    def calculate_job_run_time(target_datetime: datetime) -> datetime:
        """Calculate when to run job (21600 minutes before reservation)"""
        return target_datetime - timedelta(
            minutes=ReservationService.MAX_RESERVATION_MINUTES
        )

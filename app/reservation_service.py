import pickle
import re
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup


class ReservationService:
    """Multi-user reservation service"""

    BASE_URL = "https://klient.zatokasportu.pl"
    SERVICE_ID = "33676"
    COURT_IDS = [34631, 34632, 34633, 34634]
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

    def make_continuous_reservations(
        self, start_datetime: datetime, hours: int, num_courts: int = 1
    ) -> List[Dict]:
        """
        Make continuous reservations for the specified hours.
        Courts can change between hours, but we try to keep the same courts for continuity.
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

        # Generate court index combinations, prioritizing sequential courts
        court_indices = list(range(4))
        court_combinations = list(combinations(court_indices, num_courts))

        def is_sequential(combo):
            if len(combo) == 1:
                return True
            return all(combo[i + 1] - combo[i] == 1 for i in range(len(combo) - 1))

        sequential = [c for c in court_combinations if is_sequential(c)]
        non_sequential = [c for c in court_combinations if not is_sequential(c)]
        sorted_combinations = sequential + non_sequential

        # Track ALL reservations created during this call
        all_created_reservations = []
        results = []
        last_used_courts = None

        # Try to book each hour consecutively
        for hour in range(hours):
            current_time = start_datetime + timedelta(hours=hour)
            hour_booked = False

            # If we used specific courts in the previous hour, try to continue on them first
            if last_used_courts is not None:
                hour_results = []
                all_success = True

                for court_index in last_used_courts:
                    court_id = self.COURT_IDS[court_index]
                    result = self.make_single_reservation(current_time, court_id)
                    result_with_meta = {
                        **result,
                        "court": court_index + 1,
                        "hour_index": hour,
                    }

                    if result["success"]:
                        hour_results.append(result_with_meta)
                        all_created_reservations.append(result_with_meta)
                    else:
                        all_success = False
                        break

                if all_success:
                    results.extend(hour_results)
                    hour_booked = True

            # If preferred courts didn't work, try any available combination
            if not hour_booked:
                for court_combo in sorted_combinations:
                    hour_results = []
                    all_success = True

                    for court_index in court_combo:
                        court_id = self.COURT_IDS[court_index]
                        result = self.make_single_reservation(current_time, court_id)
                        result_with_meta = {
                            **result,
                            "court": court_index + 1,
                            "hour_index": hour,
                        }

                        if result["success"]:
                            hour_results.append(result_with_meta)
                            all_created_reservations.append(result_with_meta)
                        else:
                            all_success = False
                            break

                    if all_success:
                        results.extend(hour_results)
                        last_used_courts = court_combo
                        hour_booked = True
                        break

            # If we couldn't book this hour at all, the entire attempt failed
            if not hour_booked:
                # Clean up ALL reservations created in this call
                self._cleanup_unwanted_reservations([], all_created_reservations)
                return [
                    {
                        "success": False,
                        "message": f"Could not find available courts for hour {hour + 1} at {current_time.strftime('%H:%M')}",
                        "datetime": current_time,
                        "court_id": None,
                        "court": None,
                    }
                ]

        # Successfully booked all hours!
        # Clean up any unwanted reservations from failed court attempts
        self._cleanup_unwanted_reservations(results, all_created_reservations)
        return results

    def find_slot_in_time_window(
        self, date: datetime, start_time_str: str, end_time_str: str, hours: int, num_courts: int = 1
    ) -> List[Dict]:
        """
        Search for available slot within a time window on a specific date.
        Tries every 30-minute interval from start_time to end_time.
        Returns empty list if no slot found.
        """
        # Parse start and end times
        start_h, start_m = map(int, start_time_str.split(':'))
        end_h, end_m = map(int, end_time_str.split(':'))

        # Create datetime objects for the search window
        search_start = date.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        search_end = date.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

        # Calculate the latest possible start time for the requested hours
        required_duration = timedelta(hours=hours)
        latest_start = search_end - required_duration

        # Try every 30-minute slot within the window
        current_slot = search_start
        while current_slot <= latest_start:
            results = self.make_continuous_reservations(current_slot, hours, num_courts)

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

    def _scrape_reservations(self) -> List[Dict]:
        """Scrape user's reservations page and return list of reservations"""
        try:
            url = f"{self.BASE_URL}/index.php?s=moje_konto_zajecia"
            response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            reservations = []

            # Find all table rows in the reservations table
            table = soup.find("table", id="pricelist")
            if not table:
                return []

            rows = table.find("tbody").find_all("tr")

            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue

                # Extract date and time (format: "13.01.2026 06:30")
                date_cell = cells[1]  # data-content="Data"
                date_text = date_cell.get_text(strip=True)

                # Extract court name and number (from "Badminton - online / Kort 1 >> ...")
                name_cell = cells[2]  # data-content="Nazwa"
                name_text = name_cell.get_text(strip=True)

                # Extract reservation ID
                id_cell = cells[3]  # data-content="ID rezerwacji"
                reservation_id = id_cell.get_text(strip=True)

                # Parse court number from text like "Kort 1"
                court_match = re.search(r"Kort\s+(\d+)", name_text)
                court_number = int(court_match.group(1)) if court_match else None

                # Parse datetime (format: "DD.MM.YYYY HH:MM")
                try:
                    reservation_dt = datetime.strptime(date_text, "%d.%m.%Y %H:%M")
                except:
                    continue

                reservations.append(
                    {
                        "reservation_id": reservation_id,
                        "datetime": reservation_dt,
                        "court_number": court_number,
                        "raw_text": name_text,
                    }
                )

            return reservations

        except Exception as e:
            # Silently fail - don't break the booking if scraping fails
            return []

    def _cleanup_unwanted_reservations(
        self, successful_results: List[Dict], all_created_reservations: List[Dict]
    ) -> int:
        """
        Cancel unwanted reservations that were made during THIS API call but are not in the final successful result.
        Only cancels reservations created during failed combination attempts in this call.
        Returns the number of reservations cancelled.
        """
        if not successful_results or not all_created_reservations:
            return 0

        try:
            # Build a set of (datetime, court_number) tuples for successful reservations
            successful_slots = set()
            for result in successful_results:
                dt = result["datetime"]
                court = result.get("court")
                if dt and court:
                    successful_slots.add((dt, court))

            # Find reservations created in THIS call that are NOT in the final successful results
            to_cancel_by_slot = []
            for created in all_created_reservations:
                dt = created["datetime"]
                court = created.get("court")

                # If this reservation is not in the successful final result, we need to cancel it
                if (dt, court) not in successful_slots:
                    to_cancel_by_slot.append((dt, court))

            # If nothing to cancel, we're done
            if not to_cancel_by_slot:
                return 0

            # Scrape the reservations page to get reservation IDs
            all_reservations = self._scrape_reservations()
            if not all_reservations:
                return 0

            # Match the reservations we want to cancel with their IDs
            to_cancel_ids = []
            for reservation in all_reservations:
                res_dt = reservation["datetime"]
                res_court = reservation["court_number"]

                if (res_dt, res_court) in to_cancel_by_slot:
                    to_cancel_ids.append(reservation["reservation_id"])

            # Cancel the unwanted reservations
            cancelled_count = 0
            for reservation_id in to_cancel_ids:
                try:
                    cancel_url = f"{self.BASE_URL}/index.php?s=moje_konto_zajecia&a=anuluj&id={reservation_id}"
                    response = self.session.get(cancel_url, timeout=10)
                    if response.status_code == 200:
                        cancelled_count += 1
                except:
                    # Continue even if one cancellation fails
                    continue

            return cancelled_count

        except Exception as e:
            # Silently fail - don't break the booking if cleanup fails
            return 0

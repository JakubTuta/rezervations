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
                return {
                    "success": not result.get("error", True),
                    "message": result.get("msg", ""),
                    "datetime": dt_start,
                    "court_id": court_id,
                }
            else:
                return {
                    "success": False,
                    "message": f"HTTP {response.status_code}",
                    "datetime": dt_start,
                    "court_id": court_id,
                }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "datetime": dt_start,
                "court_id": court_id,
            }

    def make_continuous_reservations(
        self, start_datetime: datetime, hours: int, num_courts: int = 1
    ) -> List[Dict]:
        """Make continuous reservations on num_courts courts simultaneously"""
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

        # Generate court index combinations
        court_indices = list(range(4))
        court_combinations = list(combinations(court_indices, num_courts))

        # Prioritize sequential courts (adjacent courts)
        def is_sequential(combo):
            if len(combo) == 1:
                return True
            return all(combo[i+1] - combo[i] == 1 for i in range(len(combo)-1))

        sequential = [c for c in court_combinations if is_sequential(c)]
        non_sequential = [c for c in court_combinations if not is_sequential(c)]
        sorted_combinations = sequential + non_sequential

        # Try each combination
        results = []
        for court_combo in sorted_combinations:
            results = []
            all_success = True

            # Try to book all hours on all courts in this combination
            for hour in range(hours):
                current_time = start_datetime + timedelta(hours=hour)

                # Book this hour on all courts in the combination
                for court_index in court_combo:
                    court_id = self.COURT_IDS[court_index]
                    result = self.make_single_reservation(current_time, court_id)
                    results.append({
                        **result,
                        "court": court_index + 1,
                        "hour_index": hour
                    })

                    # If any reservation fails, this combination doesn't work
                    if not result["success"]:
                        all_success = False
                        break

                # If a reservation failed, stop trying this combination
                if not all_success:
                    break

            # If all reservations succeeded on this combination, we're done!
            if all_success:
                # Clean up any unwanted reservations from failed attempts
                self._cleanup_unwanted_reservations(results)
                return results

        # If we get here, no combination worked
        # Return the last attempt's results (which will show failures)
        return results

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

            soup = BeautifulSoup(response.text, 'html.parser')
            reservations = []

            # Find all table rows in the reservations table
            table = soup.find('table', id='pricelist')
            if not table:
                return []

            rows = table.find('tbody').find_all('tr')

            for row in rows:
                cells = row.find_all('td')
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
                court_match = re.search(r'Kort\s+(\d+)', name_text)
                court_number = int(court_match.group(1)) if court_match else None

                # Parse datetime (format: "DD.MM.YYYY HH:MM")
                try:
                    reservation_dt = datetime.strptime(date_text, "%d.%m.%Y %H:%M")
                except:
                    continue

                reservations.append({
                    'reservation_id': reservation_id,
                    'datetime': reservation_dt,
                    'court_number': court_number,
                    'raw_text': name_text
                })

            return reservations

        except Exception as e:
            # Silently fail - don't break the booking if scraping fails
            return []

    def _cleanup_unwanted_reservations(self, successful_results: List[Dict]) -> int:
        """
        Cancel unwanted reservations that were made during failed combination attempts.
        Returns the number of reservations cancelled.
        """
        if not successful_results:
            return 0

        try:
            # Get all current reservations
            all_reservations = self._scrape_reservations()
            if not all_reservations:
                return 0

            # Build a set of (datetime, court_number) tuples for successful reservations
            successful_slots = set()
            for result in successful_results:
                dt = result['datetime']
                court = result.get('court')
                if dt and court:
                    successful_slots.add((dt, court))

            # Determine the time range of our booking
            min_dt = min(r['datetime'] for r in successful_results)
            max_dt = max(r['datetime'] for r in successful_results)

            # Find reservations to cancel:
            # - Within our booking time range
            # - NOT in the successful results
            to_cancel = []
            for reservation in all_reservations:
                res_dt = reservation['datetime']
                res_court = reservation['court_number']

                # Check if this reservation is within our booking range
                if min_dt <= res_dt <= max_dt:
                    # Check if it's NOT in our successful results
                    if (res_dt, res_court) not in successful_slots:
                        to_cancel.append(reservation['reservation_id'])

            # Cancel unwanted reservations
            cancelled_count = 0
            for reservation_id in to_cancel:
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

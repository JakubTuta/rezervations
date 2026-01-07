import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.reservation_service import ReservationService


class SchedulerService:
    """Manages scheduled reservation jobs with persistence"""

    def __init__(self):
        self.jobs_file = Path("data/jobs/scheduled_jobs.json")
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)

        # Configure APScheduler
        jobstores = {"default": MemoryJobStore()}
        executors = {"default": ThreadPoolExecutor(max_workers=5)}
        job_defaults = {
            "coalesce": False,
            "max_instances": 3,
            "misfire_grace_time": 300,  # 5 minutes
        }

        self.scheduler = BackgroundScheduler(
            jobstores=jobstores, executors=executors, job_defaults=job_defaults
        )

        self.job_metadata: Dict[str, dict] = {}
        self._load_jobs()
        self.scheduler.start()

    def _save_jobs(self):
        """Persist job metadata to file"""
        with open(self.jobs_file, "w") as f:
            json.dump(self.job_metadata, f, indent=2, default=str)

    def _load_jobs(self):
        """Load job metadata from file and reconstruct scheduler jobs"""
        if self.jobs_file.exists():
            with open(self.jobs_file, "r") as f:
                self.job_metadata = json.load(f)

            # Reconstruct active scheduler jobs
            for job_id, metadata in self.job_metadata.items():
                status = metadata.get("status")

                # Only reconstruct jobs that are still active
                if status not in ["scheduled", "running", "retrying"]:
                    continue

                job_type = metadata.get("job_type")
                email = metadata.get("email")
                password = metadata.get("password")
                reservation_datetime = datetime.fromisoformat(metadata.get("reservation_datetime"))
                hours = metadata.get("hours")
                num_courts = metadata.get("num_courts", 1)

                if not all([email, password, reservation_datetime, hours]):
                    continue

                try:
                    if job_type == "one-time":
                        # Reconstruct one-time reservation job
                        run_time_str = metadata.get("run_time") or metadata.get("next_retry")
                        if not run_time_str:
                            continue
                        run_time = datetime.fromisoformat(run_time_str)

                        # Only reschedule if run_time is in the future
                        if run_time > datetime.now():
                            retry_count = metadata.get("retry_count", 0)
                            self.scheduler.add_job(
                                func=self._execute_reservation_with_retry,
                                trigger="date",
                                run_date=run_time,
                                args=[job_id, email, password, reservation_datetime, hours, num_courts, retry_count],
                                id=job_id,
                                replace_existing=True,
                            )

                    elif job_type == "recurring":
                        # Reconstruct recurring cancellation watcher
                        # Check if reservation time hasn't passed
                        if datetime.now() < reservation_datetime:
                            run_time = datetime.fromisoformat(metadata.get("run_time"))
                            self.scheduler.add_job(
                                func=self._check_and_book_if_available,
                                trigger="interval",
                                minutes=30,
                                start_date=run_time if run_time > datetime.now() else datetime.now(),
                                args=[job_id, email, password, reservation_datetime, hours, num_courts],
                                id=job_id,
                                replace_existing=True,
                            )
                        else:
                            # Mark as expired
                            metadata["status"] = "expired"
                            metadata["expired_at"] = datetime.now().isoformat()

                except Exception as e:
                    # If reconstruction fails, mark job as failed
                    metadata["status"] = "error"
                    metadata["error"] = f"Failed to reconstruct job on startup: {str(e)}"

            # Save any status updates
            self._save_jobs()

    def schedule_reservation(
        self, email: str, password: str, reservation_datetime: datetime, hours: int, num_courts: int = 1
    ) -> str:
        """Schedule a reservation job with retry logic"""

        # Calculate when to run the job
        run_time = ReservationService.calculate_job_run_time(reservation_datetime)

        # Generate unique job ID
        job_id = f"reservation_{uuid.uuid4().hex[:8]}"

        # Store metadata
        self.job_metadata[job_id] = {
            "job_id": job_id,
            "job_type": "one-time",
            "email": email,
            "password": password,  # Store password for job reconstruction
            "reservation_datetime": reservation_datetime.isoformat(),
            "run_time": run_time.isoformat(),
            "hours": hours,
            "num_courts": num_courts,
            "status": "scheduled",
            "created_at": datetime.now().isoformat(),
            "retry_count": 0,
            "max_retries": 6,
        }
        self._save_jobs()

        # Schedule the job
        self.scheduler.add_job(
            func=self._execute_reservation_with_retry,
            trigger="date",
            run_date=run_time,
            args=[job_id, email, password, reservation_datetime, hours, num_courts],
            id=job_id,
            replace_existing=True,
        )

        return job_id

    def _execute_reservation_with_retry(
        self,
        job_id: str,
        email: str,
        password: str,
        reservation_datetime: datetime,
        hours: int,
        num_courts: int = 1,
        retry_count: int = 0,
    ):
        """Execute reservation with exponential backoff retry"""

        metadata = self.job_metadata.get(job_id, {})
        metadata["status"] = "running"
        metadata["retry_count"] = retry_count
        metadata["last_attempt"] = datetime.now().isoformat()
        self._save_jobs()

        try:
            # Create service and make reservations
            service = ReservationService(email, password)
            results = service.make_continuous_reservations(reservation_datetime, hours, num_courts)

            # Check if all succeeded
            all_success = all(r.get("success", False) for r in results)

            if all_success:
                metadata["status"] = "completed"
                metadata["completed_at"] = datetime.now().isoformat()
                metadata["results"] = [
                    {
                        "datetime": r["datetime"].isoformat(),
                        "court": r["court"],
                        "court_id": r["court_id"],
                        "success": r["success"],
                        "message": r.get("message", ""),
                    }
                    for r in results
                ]
                self._save_jobs()
            else:
                # Retry logic with exponential backoff
                max_retries = metadata.get("max_retries", 6)

                if retry_count < max_retries:
                    # Calculate delay: 1min, 2min, 4min, 8min, 16min, 32min
                    delay_minutes = 2**retry_count
                    next_run = datetime.now() + timedelta(minutes=delay_minutes)

                    # Schedule retry
                    self.scheduler.add_job(
                        func=self._execute_reservation_with_retry,
                        trigger="date",
                        run_date=next_run,
                        args=[
                            job_id,
                            email,
                            password,
                            reservation_datetime,
                            hours,
                            num_courts,
                            retry_count + 1,
                        ],
                        id=f"{job_id}_retry_{retry_count + 1}",
                        replace_existing=True,
                    )

                    metadata["status"] = "retrying"
                    metadata["next_retry"] = next_run.isoformat()
                    self._save_jobs()
                else:
                    metadata["status"] = "failed"
                    metadata["failed_at"] = datetime.now().isoformat()
                    metadata["results"] = [
                        {
                            "datetime": r["datetime"].isoformat(),
                            "court": r.get("court"),
                            "court_id": r["court_id"],
                            "success": r["success"],
                            "message": r.get("message", ""),
                        }
                        for r in results
                    ]
                    self._save_jobs()

        except Exception as e:
            metadata["status"] = "error"
            metadata["error"] = str(e)
            self._save_jobs()

    def _sanitize_job_metadata(self, job: dict) -> dict:
        """Remove sensitive information from job metadata before returning to API"""
        sanitized = job.copy()
        sanitized.pop("password", None)  # Never expose password in API responses
        return sanitized

    def get_job_status(self, job_id: str) -> Optional[dict]:
        """Get status of a scheduled job (sanitized, without password)"""
        job = self.job_metadata.get(job_id)
        if job:
            return self._sanitize_job_metadata(job)
        return None

    def get_jobs_by_email(self, email: str) -> list:
        """Get all jobs for a specific email (sanitized, without passwords)"""
        return [
            self._sanitize_job_metadata(job)
            for job in self.job_metadata.values()
            if job.get("email") == email
        ]

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a scheduled job"""
        if job_id not in self.job_metadata:
            return False

        # Update metadata
        self.job_metadata[job_id]["status"] = "cancelled"
        self.job_metadata[job_id]["cancelled_at"] = datetime.now().isoformat()
        self._save_jobs()

        # Remove from scheduler
        try:
            self.scheduler.remove_job(job_id)
            return True
        except:
            # Job might not exist in scheduler (already executed)
            return True

    def schedule_cancellation_watcher(
        self, email: str, password: str, reservation_datetime: datetime, hours: int, num_courts: int = 1
    ) -> str:
        """Schedule a recurring job that watches for cancellations every 30 minutes"""

        # Generate unique job ID
        job_id = f"watcher_{uuid.uuid4().hex[:8]}"

        # Calculate next run time aligned to XX:00 or XX:30
        now = datetime.now()
        if now.minute < 30:
            next_run = now.replace(minute=30, second=0, microsecond=0)
        else:
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # Store metadata
        self.job_metadata[job_id] = {
            "job_id": job_id,
            "job_type": "recurring",
            "email": email,
            "password": password,  # Store password for job reconstruction
            "reservation_datetime": reservation_datetime.isoformat(),
            "run_time": next_run.isoformat(),
            "hours": hours,
            "num_courts": num_courts,
            "status": "scheduled",
            "created_at": datetime.now().isoformat(),
            "check_count": 0,
        }
        self._save_jobs()

        # Schedule the recurring job (every 30 minutes)
        self.scheduler.add_job(
            func=self._check_and_book_if_available,
            trigger="interval",
            minutes=30,
            start_date=next_run,
            args=[job_id, email, password, reservation_datetime, hours, num_courts],
            id=job_id,
            replace_existing=True,
        )

        return job_id

    def _check_and_book_if_available(
        self,
        job_id: str,
        email: str,
        password: str,
        reservation_datetime: datetime,
        hours: int,
        num_courts: int = 1,
    ):
        """Check if slots are available and book them, or stop if time has passed"""

        metadata = self.job_metadata.get(job_id, {})

        # Check if job was cancelled
        if metadata.get("status") == "cancelled":
            try:
                self.scheduler.remove_job(job_id)
            except:
                pass
            return

        # Check if current time is past the reservation time
        if datetime.now() >= reservation_datetime:
            metadata["status"] = "expired"
            metadata["expired_at"] = datetime.now().isoformat()
            self._save_jobs()
            try:
                self.scheduler.remove_job(job_id)
            except:
                pass
            return

        # Increment check count
        check_count = metadata.get("check_count", 0) + 1
        metadata["check_count"] = check_count
        metadata["last_check"] = datetime.now().isoformat()
        metadata["status"] = "running"
        self._save_jobs()

        try:
            # Create service and try to make reservations
            service = ReservationService(email, password)
            results = service.make_continuous_reservations(reservation_datetime, hours, num_courts)

            # Check if all succeeded
            all_success = all(r.get("success", False) for r in results)

            if all_success:
                # Success! Stop the recurring job
                metadata["status"] = "completed"
                metadata["completed_at"] = datetime.now().isoformat()
                metadata["results"] = [
                    {
                        "datetime": r["datetime"].isoformat(),
                        "court": r["court"],
                        "court_id": r["court_id"],
                        "success": r["success"],
                        "message": r.get("message", ""),
                    }
                    for r in results
                ]
                self._save_jobs()

                try:
                    self.scheduler.remove_job(job_id)
                except:
                    pass
            else:
                # Failed, will try again in 30 minutes
                metadata["status"] = "scheduled"
                self._save_jobs()

        except Exception as e:
            metadata["last_error"] = str(e)
            metadata["status"] = "scheduled"  # Will retry in 30 minutes
            self._save_jobs()

    def shutdown(self):
        """Shutdown scheduler gracefully"""
        self.scheduler.shutdown()


# Global singleton
scheduler_service = SchedulerService()

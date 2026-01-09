from datetime import datetime, timedelta

from fastapi import APIRouter

from app.models import (
    ReservationResponse,
    ReservationResult,
    Route1Request,
    Route2Request,
    Route3Request,
    ScheduledJobInfo,
)
from app.reservation_service import ReservationService
from app.scheduler_service import scheduler_service
from app.session_manager import session_manager
from app.utils import get_today_date_str, parse_date_time

router = APIRouter(prefix="/api/reservations", tags=["reservations"])


@router.post("/continuous", response_model=ReservationResponse)
async def make_continuous_reservations(request: Route1Request):
    """
    Route 1: Make continuous reservations for specified date/time/hours.
    Cycles through 4 courts, max 1 court at a time.
    """

    try:
        # Parse datetime
        reservation_datetime = parse_date_time(request.date, request.start_time)

        # Acquire lock for this email to prevent concurrent requests
        lock = session_manager.get_lock(request.email)

        with lock:
            # Get service instance
            service = session_manager.get_service(request.email, request.password)

            # Check if within booking window
            if not ReservationService.is_within_booking_window(reservation_datetime):
                # Schedule for later
                job_id = scheduler_service.schedule_reservation(
                    email=request.email,
                    password=request.password,
                    reservation_datetime=reservation_datetime,
                    hours=request.hours,
                    num_courts=request.num_courts,
                )

                job_info = scheduler_service.get_job_status(job_id)
                run_time = datetime.fromisoformat(job_info["run_time"])

                return ReservationResponse(
                    error=False,
                    message=f"Reservation scheduled for execution at {run_time.strftime('%Y-%m-%d %H:%M')}",
                    reservations=[],
                    scheduled_jobs=[
                        ScheduledJobInfo(
                            job_id=job_id,
                            job_type=job_info.get("job_type", "one-time"),
                            scheduled_for=job_info["run_time"],
                            reservation_datetime=job_info["reservation_datetime"],
                            hours=request.hours,
                            num_courts=request.num_courts,
                            status="scheduled",
                            email=request.email,
                            created_at=job_info.get("created_at"),
                        )
                    ],
                    stats={"successful": 0, "failed": 0, "scheduled": 1},
                )

            # Make immediate reservations
            # If end_time is specified, search within the time window
            if request.end_time:
                results = service.find_slot_in_time_window(
                    reservation_datetime,
                    request.start_time,
                    request.end_time,
                    request.hours,
                    request.num_courts,
                )
            else:
                # Try to book at the exact start time
                results = service.make_continuous_reservations(
                    reservation_datetime, request.hours, request.num_courts
                )

            # Convert to response format
            reservation_results = []
            for r in results:
                dt = r["datetime"]
                dt_end = dt + timedelta(hours=1)
                reservation_results.append(
                    ReservationResult(
                        date=dt.strftime("%d-%m-%Y"),
                        time_slot=f"{dt.strftime('%H:%M')}-{dt_end.strftime('%H:%M')}",
                        court=r["court"],
                        court_id=r["court_id"],
                        success=r["success"],
                        error_message=r.get("message") if not r["success"] else None,
                    )
                )

            successful = sum(1 for r in results if r["success"])
            failed = len(results) - successful

            return ReservationResponse(
                error=failed > 0,
                message=f"Completed {successful}/{len(results)} reservations",
                reservations=reservation_results,
                scheduled_jobs=[],
                stats={"successful": successful, "failed": failed, "scheduled": 0},
            )

    except Exception as e:
        return ReservationResponse(
            error=True,
            message=f"Internal error: {str(e)}",
            reservations=[],
            scheduled_jobs=[],
            stats={"successful": 0, "failed": 0, "scheduled": 0},
        )


@router.post("/find-slot", response_model=ReservationResponse)
async def find_available_slot(request: Route2Request):
    """
    Route 2: Find and book the first available continuous slot after given time.
    Tries across days until finding a day where all hours fit continuously.
    """

    try:
        # Parse start date (default to today)
        date_str = request.date or get_today_date_str()
        current_datetime = parse_date_time(date_str, request.start_time)

        # If no date was provided and the calculated datetime is in the past, use tomorrow
        if not request.date and current_datetime < datetime.now():
            current_datetime += timedelta(days=1)

        # Acquire lock for this email
        lock = session_manager.get_lock(request.email)

        with lock:
            service = session_manager.get_service(request.email, request.password)

            # Try up to 15 days (max booking window)
            max_attempts = 15
            attempt = 0

            while attempt < max_attempts:
                # Check if this slot is within booking window
                if not ReservationService.is_within_booking_window(current_datetime):
                    # Schedule job and return
                    job_id = scheduler_service.schedule_reservation(
                        email=request.email,
                        password=request.password,
                        reservation_datetime=current_datetime,
                        hours=request.hours,
                        num_courts=request.num_courts,
                    )

                    job_info = scheduler_service.get_job_status(job_id)

                    return ReservationResponse(
                        error=False,
                        message=f"Found slot at {current_datetime.strftime('%Y-%m-%d %H:%M')}, scheduled for booking",
                        reservations=[],
                        scheduled_jobs=[
                            ScheduledJobInfo(
                                job_id=job_id,
                                job_type=job_info.get("job_type", "one-time"),
                                scheduled_for=job_info["run_time"],
                                reservation_datetime=job_info["reservation_datetime"],
                                hours=request.hours,
                                num_courts=request.num_courts,
                                status="scheduled",
                                email=request.email,
                                created_at=job_info.get("created_at"),
                            )
                        ],
                        stats={"successful": 0, "failed": 0, "scheduled": 1},
                    )

                # Try to make reservations
                # If end_time is specified, search within the time window on this day
                if request.end_time:
                    results = service.find_slot_in_time_window(
                        current_datetime,
                        request.start_time,
                        request.end_time,
                        request.hours,
                        request.num_courts,
                    )
                else:
                    results = service.make_continuous_reservations(
                        current_datetime, request.hours, request.num_courts
                    )

                # Check if all succeeded (continuous requirement)
                all_success = results and all(r.get("success", False) for r in results)

                if all_success:
                    # Success! Return results
                    reservation_results = []
                    for r in results:
                        dt = r["datetime"]
                        dt_end = dt + timedelta(hours=1)
                        reservation_results.append(
                            ReservationResult(
                                date=dt.strftime("%d-%m-%Y"),
                                time_slot=f"{dt.strftime('%H:%M')}-{dt_end.strftime('%H:%M')}",
                                court=r["court"],
                                court_id=r["court_id"],
                                success=r["success"],
                                error_message=None,
                            )
                        )

                    return ReservationResponse(
                        error=False,
                        message=f"Successfully booked {len(results)} continuous hours at {current_datetime.strftime('%Y-%m-%d %H:%M')}",
                        reservations=reservation_results,
                        scheduled_jobs=[],
                        stats={"successful": len(results), "failed": 0, "scheduled": 0},
                    )

                # Failed - try next day at same time
                current_datetime += timedelta(days=1)
                attempt += 1

            # Exhausted attempts
            return ReservationResponse(
                error=True,
                message=f"Could not find {request.hours} continuous hours within {max_attempts} days",
                reservations=[],
                scheduled_jobs=[],
                stats={"successful": 0, "failed": 1, "scheduled": 0},
            )

    except Exception as e:
        return ReservationResponse(
            error=True,
            message=f"Internal error: {str(e)}",
            reservations=[],
            scheduled_jobs=[],
            stats={"successful": 0, "failed": 0, "scheduled": 0},
        )


@router.post("/watch-for-cancellations", response_model=ReservationResponse)
async def watch_for_cancellations(request: Route3Request):
    """
    Route 3: Watch for cancellations and book when slots become available.
    First tries to book immediately. If slots not available, creates a recurring job
    that checks every 30 minutes (at XX:00 and XX:30).
    Stops when slots are booked successfully or reservation time has passed.
    """

    try:
        # Parse datetime
        reservation_datetime = parse_date_time(request.date, request.start_time)

        # Acquire lock for this email
        lock = session_manager.get_lock(request.email)

        with lock:
            # Check if reservation time has already passed
            if datetime.now() >= reservation_datetime:
                return ReservationResponse(
                    error=True,
                    message="Reservation time has already passed",
                    reservations=[],
                    scheduled_jobs=[],
                    stats={"successful": 0, "failed": 1, "scheduled": 0},
                )

            # First, try to book immediately
            service = session_manager.get_service(request.email, request.password)

            # If end_time is specified, search within the time window
            if request.end_time:
                results = service.find_slot_in_time_window(
                    reservation_datetime,
                    request.start_time,
                    request.end_time,
                    request.hours,
                    request.num_courts,
                )
            else:
                # Try to book at the exact start time
                results = service.make_continuous_reservations(
                    reservation_datetime, request.hours, request.num_courts
                )

            # Check if booking was successful
            if results and all(r.get("success", False) for r in results):
                # Successfully booked! Return results
                reservation_results = []
                for r in results:
                    dt = r["datetime"]
                    dt_end = dt + timedelta(hours=1)
                    reservation_results.append(
                        ReservationResult(
                            date=dt.strftime("%d-%m-%Y"),
                            time_slot=f"{dt.strftime('%H:%M')}-{dt_end.strftime('%H:%M')}",
                            court=r["court"],
                            court_id=r["court_id"],
                            success=r["success"],
                            error_message=None,
                        )
                    )

                return ReservationResponse(
                    error=False,
                    message=f"Slots were available! Successfully booked {len(results)} hours",
                    reservations=reservation_results,
                    scheduled_jobs=[],
                    stats={"successful": len(results), "failed": 0, "scheduled": 0},
                )

            # Slots not available - create watcher job
            job_id = scheduler_service.schedule_cancellation_watcher(
                email=request.email,
                password=request.password,
                reservation_datetime=reservation_datetime,
                hours=request.hours,
                num_courts=request.num_courts,
            )

            job_info = scheduler_service.get_job_status(job_id)
            next_run = datetime.fromisoformat(job_info["run_time"])

            return ReservationResponse(
                error=False,
                message=f"Slots not currently available. Cancellation watcher started. Will check every 30 minutes starting at {next_run.strftime('%Y-%m-%d %H:%M')}",
                reservations=[],
                scheduled_jobs=[
                    ScheduledJobInfo(
                        job_id=job_id,
                        job_type="recurring",
                        scheduled_for=job_info["run_time"],
                        reservation_datetime=job_info["reservation_datetime"],
                        hours=request.hours,
                        num_courts=request.num_courts,
                        status="scheduled",
                        email=request.email,
                        created_at=job_info.get("created_at"),
                    )
                ],
                stats={"successful": 0, "failed": 0, "scheduled": 1},
            )

    except Exception as e:
        return ReservationResponse(
            error=True,
            message=f"Internal error: {str(e)}",
            reservations=[],
            scheduled_jobs=[],
            stats={"successful": 0, "failed": 0, "scheduled": 0},
        )


@router.get("/jobs")
async def get_user_jobs(email: str):
    """Get all scheduled jobs for a specific email"""
    try:
        jobs = scheduler_service.get_jobs_by_email(email)

        # Convert to ScheduledJobInfo format
        job_list = []
        for job in jobs:
            job_list.append(
                {
                    "job_id": job.get("job_id"),
                    "job_type": job.get("job_type", "one-time"),
                    "scheduled_for": job.get("run_time"),
                    "reservation_datetime": job.get("reservation_datetime"),
                    "hours": job.get("hours"),
                    "num_courts": job.get("num_courts", 1),
                    "status": job.get("status"),
                    "email": job.get("email"),
                    "created_at": job.get("created_at"),
                    "check_count": job.get("check_count"),
                    "last_check": job.get("last_check"),
                    "retry_count": job.get("retry_count"),
                    "next_retry": job.get("next_retry"),
                }
            )

        return {
            "error": False,
            "email": email,
            "total_jobs": len(job_list),
            "jobs": job_list,
        }
    except Exception as e:
        return {"error": True, "message": f"Internal error: {str(e)}", "jobs": []}


@router.delete("/job/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a scheduled job"""
    try:
        success = scheduler_service.cancel_job(job_id)
        if success:
            return {"error": False, "message": f"Job {job_id} cancelled successfully"}
        else:
            return {"error": True, "message": f"Job {job_id} not found"}
    except Exception as e:
        return {"error": True, "message": f"Internal error: {str(e)}"}


@router.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a scheduled job"""
    status = scheduler_service.get_job_status(job_id)
    if status:
        return {"error": False, "job": status}
    return {"error": True, "message": "Job not found"}

from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator
from typing import Optional, List
from datetime import datetime


class Route1Request(BaseModel):
    """Simple continuous reservation request"""
    date: str = Field(..., description="DD-MM-YYYY format")
    start_time: str = Field(..., description="HH:MM format (must be XX:30)")
    hours: int = Field(..., gt=0, description="Number of hours to reserve")
    end_time: Optional[str] = Field(None, description="HH:MM format (must be XX:30) - optional search window upper limit")
    num_courts: int = Field(1, ge=1, le=4, description="Number of courts to reserve (1-4)")
    email: EmailStr
    password: str

    @field_validator('start_time', 'end_time')
    @classmethod
    def validate_time_format(cls, v):
        if v and not v.endswith(':30'):
            raise ValueError('Time must be in XX:30 format')
        return v

    @model_validator(mode='after')
    def validate_time_window(self):
        # If end_time is provided, validate it defines a valid search window
        if self.end_time:
            start_h, start_m = map(int, self.start_time.split(':'))
            end_h, end_m = map(int, self.end_time.split(':'))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if end_minutes <= start_minutes:
                raise ValueError('End time must be after start time')

            # Validate that the time window can fit the requested hours
            window_minutes = end_minutes - start_minutes
            required_minutes = self.hours * 60

            if window_minutes < required_minutes:
                raise ValueError(
                    f'Time window ({window_minutes // 60}h) is too small for {self.hours} hours'
                )

        return self


class Route2Request(BaseModel):
    """Find any continuous slot after given time"""
    start_time: str = Field(..., description="HH:MM format (must be XX:30)")
    hours: int = Field(..., gt=0, description="Number of hours to reserve")
    end_time: Optional[str] = Field(None, description="HH:MM format (must be XX:30) - optional search window upper limit")
    num_courts: int = Field(1, ge=1, le=4, description="Number of courts to reserve (1-4)")
    email: EmailStr
    password: str
    date: Optional[str] = Field(None, description="DD-MM-YYYY format, defaults to today")

    @field_validator('start_time', 'end_time')
    @classmethod
    def validate_time_format(cls, v):
        if v and not v.endswith(':30'):
            raise ValueError('Time must be in XX:30 format')
        return v

    @model_validator(mode='after')
    def validate_time_window(self):
        # If end_time is provided, validate it defines a valid search window
        if self.end_time:
            start_h, start_m = map(int, self.start_time.split(':'))
            end_h, end_m = map(int, self.end_time.split(':'))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if end_minutes <= start_minutes:
                raise ValueError('End time must be after start time')

            # Validate that the time window can fit the requested hours
            window_minutes = end_minutes - start_minutes
            required_minutes = self.hours * 60

            if window_minutes < required_minutes:
                raise ValueError(
                    f'Time window ({window_minutes // 60}h) is too small for {self.hours} hours'
                )

        return self


class Route3Request(BaseModel):
    """Watch for cancellations and book when available"""
    date: str = Field(..., description="DD-MM-YYYY format")
    start_time: str = Field(..., description="HH:MM format (must be XX:30)")
    hours: int = Field(..., gt=0, description="Number of hours to reserve")
    end_time: Optional[str] = Field(None, description="HH:MM format (must be XX:30) - optional search window upper limit")
    num_courts: int = Field(1, ge=1, le=4, description="Number of courts to reserve (1-4)")
    email: EmailStr
    password: str

    @field_validator('start_time', 'end_time')
    @classmethod
    def validate_time_format(cls, v):
        if v and not v.endswith(':30'):
            raise ValueError('Time must be in XX:30 format')
        return v

    @model_validator(mode='after')
    def validate_time_window(self):
        # If end_time is provided, validate it defines a valid search window
        if self.end_time:
            start_h, start_m = map(int, self.start_time.split(':'))
            end_h, end_m = map(int, self.end_time.split(':'))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if end_minutes <= start_minutes:
                raise ValueError('End time must be after start time')

            # Validate that the time window can fit the requested hours
            window_minutes = end_minutes - start_minutes
            required_minutes = self.hours * 60

            if window_minutes < required_minutes:
                raise ValueError(
                    f'Time window ({window_minutes // 60}h) is too small for {self.hours} hours'
                )

        return self


class ReservationResult(BaseModel):
    """Single reservation result"""
    date: str
    time_slot: str  # e.g., "06:30-07:30"
    court: int  # 1-4
    court_id: int
    success: bool
    error_message: Optional[str] = None


class ScheduledJobInfo(BaseModel):
    """Info about a scheduled job"""
    job_id: str
    job_type: str  # "one-time" or "recurring"
    scheduled_for: str  # ISO datetime when job will run (or next run for recurring)
    reservation_datetime: str  # The actual reservation datetime
    hours: int
    num_courts: int = 1  # Number of courts to reserve (1-4)
    status: str  # "scheduled", "running", "completed", "failed", "cancelled"
    email: Optional[str] = None  # Owner email
    created_at: Optional[str] = None
    next_retry: Optional[str] = None  # For one-time jobs with retries


class ReservationResponse(BaseModel):
    """API response for reservation requests"""
    error: bool
    message: str
    reservations: List[ReservationResult] = []
    scheduled_jobs: List[ScheduledJobInfo] = []
    stats: dict = {}  # {"successful": N, "failed": N, "scheduled": N}

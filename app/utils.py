from datetime import datetime


def validate_time_format(time_str: str) -> bool:
    """Validate that time is in XX:30 format"""
    return time_str.endswith(":30")


def parse_date_time(date_str: str, time_str: str) -> datetime:
    """Parse DD-MM-YYYY and HH:MM to datetime"""
    datetime_str = f"{date_str} {time_str}"
    return datetime.strptime(datetime_str, "%d-%m-%Y %H:%M")


def get_today_date_str() -> str:
    """Get today's date in DD-MM-YYYY format"""
    return datetime.now().strftime("%d-%m-%Y")

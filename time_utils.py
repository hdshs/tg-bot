from datetime import datetime


def is_valid_hhmm(text: str) -> bool:
    if len(text) != 5 or text[2] != ":":
        return False
    hh, mm = text.split(":")
    if not (hh.isdigit() and mm.isdigit()):
        return False
    hh = int(hh)
    mm = int(mm)
    return 0 <= hh <= 23 and 0 <= mm <= 59


def normalize_hhmm(text: str) -> str:
    hh, mm = text.split(":")
    return f"{int(hh):02d}:{int(mm):02d}"


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_hhmm():
    return datetime.now().strftime("%H:%M")

from datetime import datetime

from app.models.reservation import ACTIVE_RESERVATION_STATUSES, Reservation


def intervals_overlap(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    return first_start < second_end and first_end > second_start


def shift_window_to_now(
    start_time: datetime, end_time: datetime, now: datetime
) -> tuple[datetime, datetime]:
    if now <= start_time:
        return start_time, end_time
    duration = end_time - start_time
    return now, now + duration


def has_reservation_conflict(
    reservations: list[Reservation],
    charger_id: str,
    port_number: int,
    start_time: datetime,
    end_time: datetime,
    ignore_reservation_id: str | None = None,
) -> bool:
    return any(
        reservation.reservation_id != ignore_reservation_id
        and reservation.charger_id == charger_id
        and reservation.port_number == port_number
        and reservation.status in ACTIVE_RESERVATION_STATUSES
        and intervals_overlap(
            start_time, end_time, reservation.start_time, reservation.end_time
        )
        for reservation in reservations
    )

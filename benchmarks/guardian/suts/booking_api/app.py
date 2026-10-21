"""8-operation Booking API，覆盖预订领域的受控回归。."""

from fastapi import FastAPI

from benchmarks.guardian.suts.common import BenchmarkOperation, create_catalog_app

_OPERATIONS = (
    BenchmarkOperation(
        "/bookings/primary",
        "getPrimaryBooking",
        {"id": "booking-1", "guest": "Ada", "nights": 2, "status": "confirmed"},
        {
            "booking_id_type_lag": {"id": "booking-1", "guest": "Ada", "nights": 2, "status": "confirmed"},
            "booking_guest_missing": {"id": 1, "nights": 2, "status": "confirmed"},
            "wrong_booking_semantic": {"id": 999, "guest": "Wrong Guest", "nights": 2, "status": "confirmed"},
            "clean_control": {"id": 1, "guest": "Ada", "nights": 2, "status": "confirmed"},
        },
    ),
    BenchmarkOperation(
        "/bookings",
        "listBookings",
        [{"id": "booking-1", "guest": "Ada", "nights": 2, "status": "confirmed"}],
        {"booking_list_id_type_lag": [{"id": "booking-1", "guest": "Ada", "nights": 2, "status": "confirmed"}]},
    ),
    BenchmarkOperation(
        "/rooms/primary",
        "getPrimaryRoom",
        {"id": "room-1", "name": "Ocean", "capacity": 2},
        {"room_name_missing": {"id": 1, "capacity": 2}},
    ),
    BenchmarkOperation(
        "/rooms",
        "listRooms",
        [{"id": "room-1", "name": "Ocean", "capacity": 2}],
        {"room_list_id_type_lag": [{"id": "room-1", "name": "Ocean", "capacity": 2}]},
    ),
    BenchmarkOperation(
        "/guests/primary",
        "getPrimaryGuest",
        {"id": "guest-1", "name": "Ada", "vip": False},
        {"guest_id_type_lag": {"id": "guest-1", "name": "Ada", "vip": False}},
    ),
    BenchmarkOperation(
        "/payments/latest",
        "getLatestPayment",
        {"id": "payment-1", "amount": 200.0, "currency": "USD"},
        {
            "payment_amount_type_lag": {"id": "payment-1", "amount": 200.0, "currency": "USD"},
            "wrong_payment_semantic": {"id": 1, "amount": 0, "currency": "USD"},
        },
    ),
    BenchmarkOperation(
        "/availability/current",
        "getCurrentAvailability",
        {"id": "availability-1", "rooms": 4, "date": "2026-09-03"},
        {"availability_rooms_type_lag": {"id": "availability-1", "rooms": 4, "date": "2026-09-03"}},
    ),
    BenchmarkOperation(
        "/status",
        "getBookingStatus",
        {"status": "ok", "region": "local"},
        {"booking_status_enum_lag": {"status": "degraded", "region": 1}},
    ),
)


def create_app(variant: str) -> FastAPI:
    """创建一次隔离的 Booking SUT。."""
    return create_catalog_app("Guardian Benchmark Booking API", variant, _OPERATIONS)

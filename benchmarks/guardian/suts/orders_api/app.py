"""8-operation Orders API，行为由固定 mutation variant 控制。."""

from fastapi import FastAPI

from benchmarks.guardian.suts.common import RequestCounter, attach_benchmark_control


def create_app(variant: str) -> FastAPI:
    """为单个 case 创建干净、可计数的内存 SUT。."""
    app = FastAPI(title="Guardian Benchmark Orders API", version="1.0.0")
    counter = RequestCounter()
    attach_benchmark_control(app, counter)

    @app.get("/orders", response_model=None)
    async def list_orders() -> list[dict[str, object]]:
        if variant == "order_list_id_type_lag":
            return [{"id": "order-1", "total": 19.95, "status": "pending"}]
        return [{"id": "order-1", "total": 19.95, "status": "pending"}]

    @app.get("/orders/{order_id}", response_model=None)
    async def get_order(order_id: str) -> dict[str, object]:
        order_identifier: object = order_id
        total: object = 19.95
        status = "pending"
        if variant == "order_total_type_lag":
            order_identifier = 1
            total = 19.95
        elif variant == "wrong_total_semantic":
            order_identifier = 1
            total = 0
        if variant == "order_status_enum_lag":
            order_identifier = 1
            status = "pending"
        elif variant == "clean_control":
            order_identifier = 1
        return {"id": order_identifier, "total": total, "status": status}

    @app.get("/orders/{order_id}/summary", response_model=None)
    async def get_order_summary(order_id: str) -> dict[str, object]:
        if variant == "summary_item_count_type_lag":
            return {"order_id": order_id, "item_count": 2, "total": 19.95}
        return {"order_id": order_id, "item_count": 2, "total": 19.95}

    @app.get("/customers/{customer_id}/orders", response_model=None)
    async def list_customer_orders(customer_id: str) -> list[dict[str, object]]:
        if variant == "customer_order_id_type_lag":
            return [{"id": "order-1", "customer_id": customer_id, "total": 19.95}]
        return [{"id": "order-1", "customer_id": customer_id, "total": 19.95}]

    @app.get("/invoices/{invoice_id}", response_model=None)
    async def get_invoice(invoice_id: str) -> dict[str, object]:
        if variant == "invoice_currency_missing":
            return {"id": 1, "amount": 19.95}
        if variant == "invoice_amount_semantic":
            return {"id": 1, "amount": 0, "currency": "USD"}
        return {"id": invoice_id, "amount": 19.95, "currency": "USD"}

    @app.get("/shipments/{shipment_id}", response_model=None)
    async def get_shipment(shipment_id: str) -> dict[str, object]:
        tracking: object = "TRACK-123" if variant == "shipment_tracking_type_lag" else "TRACK-123"
        return {"id": shipment_id, "tracking_number": tracking, "status": "in_transit"}

    @app.get("/currencies", response_model=None)
    async def list_currencies() -> list[dict[str, object]]:
        if variant == "currency_symbol_missing":
            return [{"code": 1, "name": "US Dollar"}]
        return [{"code": "USD", "name": "US Dollar"}]

    @app.get("/status", response_model=None)
    async def service_status() -> dict[str, str]:
        return {"status": "ok", "region": "local"}

    return app

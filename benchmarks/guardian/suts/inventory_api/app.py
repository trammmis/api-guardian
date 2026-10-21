"""8-operation Inventory API，覆盖库存领域的受控回归。."""

from fastapi import FastAPI

from benchmarks.guardian.suts.common import BenchmarkOperation, create_catalog_app

_OPERATIONS = (
    BenchmarkOperation(
        "/stock/primary",
        "getPrimaryStock",
        {"id": "stock-1", "sku": "SKU-1", "quantity": 12, "status": "available"},
        {
            "stock_id_type_lag": {"id": "stock-1", "sku": "SKU-1", "quantity": 12, "status": "available"},
            "stock_sku_missing": {"id": 1, "quantity": 12, "status": "available"},
            "wrong_stock_semantic": {"id": 1, "sku": "SKU-1", "quantity": 999, "status": "available"},
            "clean_control": {"id": 1, "sku": "SKU-1", "quantity": 12, "status": "available"},
        },
    ),
    BenchmarkOperation(
        "/stock",
        "listStock",
        [{"id": "stock-1", "sku": "SKU-1", "quantity": 12, "status": "available"}],
        {"stock_list_id_type_lag": [{"id": "stock-1", "sku": "SKU-1", "quantity": 12, "status": "available"}]},
    ),
    BenchmarkOperation(
        "/warehouses/primary",
        "getPrimaryWarehouse",
        {"id": "warehouse-1", "name": "North", "capacity": 100},
        {"warehouse_name_missing": {"id": 1, "capacity": 100}},
    ),
    BenchmarkOperation(
        "/warehouses",
        "listWarehouses",
        [{"id": "warehouse-1", "name": "North", "capacity": 100}],
        {"warehouse_list_id_type_lag": [{"id": "warehouse-1", "name": "North", "capacity": 100}]},
    ),
    BenchmarkOperation(
        "/reservations/primary",
        "getPrimaryReservation",
        {"id": "reservation-1", "sku": "SKU-1", "quantity": 2},
        {"reservation_id_type_lag": {"id": "reservation-1", "sku": "SKU-1", "quantity": 2}},
    ),
    BenchmarkOperation(
        "/movements/latest",
        "getLatestMovement",
        {"id": "movement-1", "quantity": 4, "direction": "in"},
        {
            "movement_quantity_type_lag": {"id": "movement-1", "quantity": 4, "direction": "in"},
            "wrong_movement_semantic": {"id": 1, "quantity": -4, "direction": "in"},
        },
    ),
    BenchmarkOperation(
        "/suppliers/primary",
        "getPrimarySupplier",
        {"id": "supplier-1", "name": "Supply Co", "lead_days": 3},
        {"supplier_lead_days_type_lag": {"id": "supplier-1", "name": "Supply Co", "lead_days": 3}},
    ),
    BenchmarkOperation(
        "/status",
        "getInventoryStatus",
        {"status": "ok", "region": "local"},
        {"inventory_status_enum_lag": {"status": "degraded", "region": 1}},
    ),
)


def create_app(variant: str) -> FastAPI:
    """创建一次隔离的 Inventory SUT。."""
    return create_catalog_app("Guardian Benchmark Inventory API", variant, _OPERATIONS)

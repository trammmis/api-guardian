"""8-operation Products API，覆盖结构错误与业务语义负对照。."""

from fastapi import FastAPI

from benchmarks.guardian.suts.common import BenchmarkOperation, create_catalog_app

_OPERATIONS = (
    BenchmarkOperation(
        "/products/primary",
        "getPrimaryProduct",
        {"id": "product-1", "name": "Keyboard", "stock": 20, "status": "active"},
        {
            "product_id_type_lag": {"id": "product-1", "name": "Keyboard", "stock": 20, "status": "active"},
            "product_name_missing": {"id": 1, "stock": 20, "status": "active"},
            "wrong_product_semantic": {"id": 999, "name": "Wrong Product", "stock": 20, "status": "active"},
            "clean_control": {"id": 1, "name": "Keyboard", "stock": 20, "status": "active"},
        },
    ),
    BenchmarkOperation(
        "/products",
        "listProducts",
        [{"id": "product-1", "name": "Keyboard", "stock": 20, "status": "active"}],
        {"product_list_id_type_lag": [{"id": "product-1", "name": "Keyboard", "stock": 20, "status": "active"}]},
    ),
    BenchmarkOperation(
        "/categories/primary",
        "getPrimaryCategory",
        {"id": "category-1", "name": "Accessories", "count": 5},
        {"category_name_missing": {"id": 1, "count": 5}},
    ),
    BenchmarkOperation(
        "/categories",
        "listCategories",
        [{"id": "category-1", "name": "Accessories", "count": 5}],
        {"category_list_id_type_lag": [{"id": "category-1", "name": "Accessories", "count": 5}]},
    ),
    BenchmarkOperation(
        "/brands/primary",
        "getPrimaryBrand",
        {"id": "brand-1", "name": "Acme", "active": True},
        {"brand_id_type_lag": {"id": "brand-1", "name": "Acme", "active": True}},
    ),
    BenchmarkOperation(
        "/prices/current",
        "getCurrentPrice",
        {"id": "price-1", "amount": 19.95, "currency": "USD"},
        {
            "price_amount_type_lag": {"id": "price-1", "amount": 19.95, "currency": "USD"},
            "wrong_price_semantic": {"id": 1, "amount": 0, "currency": "USD"},
        },
    ),
    BenchmarkOperation(
        "/reviews/latest",
        "getLatestReview",
        {"id": "review-1", "rating": 5, "comment": "Useful"},
        {"review_rating_type_lag": {"id": "review-1", "rating": 5, "comment": "Useful"}},
    ),
    BenchmarkOperation(
        "/status",
        "getProductsStatus",
        {"status": "ok", "region": "local"},
        {"product_status_enum_lag": {"status": "degraded", "region": 1}},
    ),
)


def create_app(variant: str) -> FastAPI:
    """创建一次隔离的 Products SUT。."""
    return create_catalog_app("Guardian Benchmark Products API", variant, _OPERATIONS)

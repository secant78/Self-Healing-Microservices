"""
Integration-style tests for the Payment Service FastAPI application.

These tests exercise the actual routes defined in app/main.py via httpx's
AsyncClient so the full ASGI stack (middleware, validation, chaos logic) runs
on every request.

Tracing is disabled via environment variables before the app is imported so
that no OTLP connection is attempted during test runs.
"""

import os

# Disable OpenTelemetry OTLP export before any app module is imported.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Import the FastAPI application after env vars are set.
from app.main import app
from app.chaos import chaos_controller


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_chaos_state():
    """Reset the global chaos controller to defaults before every test."""
    chaos_controller.reset()
    yield
    chaos_controller.reset()


@pytest_asyncio.fixture
async def client():
    """Provide an httpx AsyncClient bound to the FastAPI ASGI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_200(client):
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_contains_status_field(client):
    response = await client.get("/health")
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_contains_service_name(client):
    response = await client.get("/health")
    data = response.json()
    assert data.get("service") == "payment-service"


@pytest.mark.asyncio
async def test_health_contains_chaos_mode(client):
    response = await client.get("/health")
    data = response.json()
    assert "chaos_mode" in data
    assert data["chaos_mode"] == "none"


# ---------------------------------------------------------------------------
# POST /payment — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_returns_200_with_valid_body(client):
    payload = {"amount": 10.0, "customer_id": "cust-1"}
    response = await client.post("/payment", json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_payment_response_contains_transaction_id(client):
    payload = {"amount": 10.0, "customer_id": "cust-1"}
    response = await client.post("/payment", json=payload)
    data = response.json()
    assert "transaction_id" in data
    assert data["transaction_id"]  # non-empty string


@pytest.mark.asyncio
async def test_payment_response_contains_expected_fields(client):
    payload = {"amount": 25.50, "customer_id": "cust-abc", "currency": "USD"}
    response = await client.post("/payment", json=payload)
    data = response.json()
    assert data["status"] == "success"
    assert data["amount"] == 25.50
    assert data["currency"] == "USD"
    assert data["customer_id"] == "cust-abc"
    assert data["chaos_mode"] == "none"


@pytest.mark.asyncio
async def test_payment_uses_default_usd_currency(client):
    payload = {"amount": 5.0, "customer_id": "cust-2"}
    response = await client.post("/payment", json=payload)
    data = response.json()
    assert data["currency"] == "USD"


@pytest.mark.asyncio
async def test_payment_validates_positive_amount(client):
    payload = {"amount": 0.0, "customer_id": "cust-3"}
    response = await client.post("/payment", json=payload)
    assert response.status_code == 422  # FastAPI validation error for amount <= 0


@pytest.mark.asyncio
async def test_payment_validates_nonempty_customer_id(client):
    payload = {"amount": 10.0, "customer_id": ""}
    response = await client.post("/payment", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_chaos_state(client):
    response = await client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "payment-service"
    assert "mode" in data
    assert "circuit_breaker_enabled" in data


@pytest.mark.asyncio
async def test_status_reflects_updated_mode(client):
    await client.post("/chaos", json={"mode": "error"})
    response = await client.get("/status")
    data = response.json()
    assert data["mode"] == "error"


# ---------------------------------------------------------------------------
# POST /chaos — set chaos mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_chaos_mode_error(client):
    response = await client.post("/chaos", json={"mode": "error"})
    assert response.status_code == 200
    data = response.json()
    assert data["state"]["mode"] == "error"


@pytest.mark.asyncio
async def test_set_chaos_mode_latency(client):
    response = await client.post("/chaos", json={"mode": "latency", "latency_ms": 100})
    assert response.status_code == 200
    data = response.json()
    assert data["state"]["mode"] == "latency"
    assert data["state"]["latency_ms"] == 100


@pytest.mark.asyncio
async def test_set_chaos_mode_none(client):
    # First set to error, then back to none
    await client.post("/chaos", json={"mode": "error"})
    response = await client.post("/chaos", json={"mode": "none"})
    assert response.status_code == 200
    data = response.json()
    assert data["state"]["mode"] == "none"


@pytest.mark.asyncio
async def test_set_chaos_message_reflects_mode(client):
    response = await client.post("/chaos", json={"mode": "latency", "latency_ms": 50})
    data = response.json()
    assert "latency" in data["message"]


# ---------------------------------------------------------------------------
# DELETE /chaos — reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_chaos_resets_to_none(client):
    await client.post("/chaos", json={"mode": "error"})
    response = await client.delete("/chaos")
    assert response.status_code == 200
    data = response.json()
    assert data["state"]["mode"] == "none"


@pytest.mark.asyncio
async def test_delete_chaos_resets_circuit_breaker(client):
    await client.post("/circuit-breaker", json={"enabled": True})
    response = await client.delete("/chaos")
    data = response.json()
    assert data["state"]["circuit_breaker_enabled"] is False


# ---------------------------------------------------------------------------
# POST /circuit-breaker — toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_enable(client):
    response = await client.post("/circuit-breaker", json={"enabled": True})
    assert response.status_code == 200
    data = response.json()
    assert data["circuit_breaker_enabled"] is True


@pytest.mark.asyncio
async def test_circuit_breaker_disable(client):
    await client.post("/circuit-breaker", json={"enabled": True})
    response = await client.post("/circuit-breaker", json={"enabled": False})
    assert response.status_code == 200
    data = response.json()
    assert data["circuit_breaker_enabled"] is False


# ---------------------------------------------------------------------------
# POST /circuit-breaker/open and /close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_open_endpoint(client):
    response = await client.post("/circuit-breaker/open")
    assert response.status_code == 200
    data = response.json()
    assert data["circuit_breaker_enabled"] is True


@pytest.mark.asyncio
async def test_circuit_breaker_close_endpoint(client):
    await client.post("/circuit-breaker/open")
    response = await client.post("/circuit-breaker/close")
    assert response.status_code == 200
    data = response.json()
    assert data["circuit_breaker_enabled"] is False


# ---------------------------------------------------------------------------
# Circuit breaker blocks payments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_returns_503_when_circuit_breaker_enabled(client):
    await client.post("/circuit-breaker", json={"enabled": True})
    response = await client.post("/payment", json={"amount": 10.0, "customer_id": "cust-x"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_payment_503_detail_contains_busy_message(client):
    await client.post("/circuit-breaker/open")
    response = await client.post("/payment", json={"amount": 10.0, "customer_id": "cust-x"})
    body = response.json()
    # FastAPI wraps HTTPException detail under "detail" key
    assert "detail" in body
    assert "circuit breaker" in body["detail"].lower() or "busy" in body["detail"].lower()


@pytest.mark.asyncio
async def test_payment_succeeds_after_circuit_breaker_closed(client):
    await client.post("/circuit-breaker/open")
    await client.post("/circuit-breaker/close")
    response = await client.post("/payment", json={"amount": 10.0, "customer_id": "cust-y"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Latency mode — payment still completes (latency_ms kept very low in tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_completes_with_latency_mode(client):
    """Set a very short latency to keep test fast while exercising the code path."""
    await client.post("/chaos", json={"mode": "latency", "latency_ms": 10})
    response = await client.post("/payment", json={"amount": 10.0, "customer_id": "cust-latency"})
    assert response.status_code == 200
    data = response.json()
    assert data["chaos_mode"] == "latency"
    assert "transaction_id" in data


@pytest.mark.asyncio
async def test_payment_chaos_mode_reflected_in_response(client):
    """Response chaos_mode field matches the currently active mode."""
    await client.post("/chaos", json={"mode": "latency", "latency_ms": 5})
    response = await client.post("/payment", json={"amount": 1.0, "customer_id": "cust-1"})
    data = response.json()
    assert data["chaos_mode"] == "latency"

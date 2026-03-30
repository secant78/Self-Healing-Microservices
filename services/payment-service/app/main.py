"""
Payment Service — FastAPI entry point.

Startup sequence
----------------
1. Load .env (if present) so OTEL env vars are available before tracing init.
2. Call configure_tracing() to set up the global TracerProvider.
3. Create the FastAPI app and instrument it with FastAPIInstrumentor.
4. Register all routes.
"""

import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load environment variables as early as possible so tracing picks them up.
load_dotenv()

from app.tracing import configure_tracing  # noqa: E402 — must come after load_dotenv

configure_tracing()

from opentelemetry import trace  # noqa: E402
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402
from typing import Literal, Optional  # noqa: E402

from app.chaos import chaos_controller  # noqa: E402

tracer = trace.get_tracer("payment-service")


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Payment amount (must be positive)")
    customer_id: str = Field(..., min_length=1, description="Unique customer identifier")
    currency: str = Field(default="USD", min_length=3, max_length=3)


class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    amount: float
    currency: str
    customer_id: str
    chaos_mode: str


class ChaosConfig(BaseModel):
    mode: Literal["none", "latency", "error"]
    latency_ms: Optional[int] = Field(default=None, ge=0)
    error_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class CircuitBreakerConfig(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nothing to initialise at startup beyond what module-level code already
    # does; the context manager is required by FastAPI ≥ 0.93.
    yield


app = FastAPI(
    title="Payment Service",
    description="Processes payments with optional chaos engineering capabilities.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow all origins so the React frontend (any port) can reach this service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument *after* the app is created so FastAPIInstrumentor can wrap it.
FastAPIInstrumentor.instrument_app(app)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/payment", response_model=PaymentResponse, status_code=200)
async def process_payment(request: PaymentRequest) -> PaymentResponse:
    """
    Process a payment request.

    Chaos is applied before the business logic so that span attributes are
    always recorded, even when a chaos error is ultimately raised.
    """
    with tracer.start_as_current_span("process_payment") as span:
        span.set_attribute("payment.amount", request.amount)
        span.set_attribute("payment.customer_id", request.customer_id)
        span.set_attribute("payment.currency", request.currency)
        span.set_attribute("payment.chaos_mode", chaos_controller.get_mode())
        span.set_attribute(
            "payment.circuit_breaker_enabled",
            chaos_controller.get_circuit_breaker_enabled(),
        )

        # May raise HTTP 503 (circuit breaker) or HTTP 500 (error chaos).
        await chaos_controller.apply_chaos()

        transaction_id = str(uuid.uuid4())
        span.set_attribute("payment.transaction_id", transaction_id)

        return PaymentResponse(
            transaction_id=transaction_id,
            status="success",
            amount=request.amount,
            currency=request.currency,
            customer_id=request.customer_id,
            chaos_mode=chaos_controller.get_mode(),
        )


@app.get("/health")
async def health_check() -> dict:
    """Return service health and the current chaos mode."""
    return {
        "status": "healthy",
        "service": "payment-service",
        "chaos_mode": chaos_controller.get_mode(),
        "circuit_breaker_enabled": chaos_controller.get_circuit_breaker_enabled(),
    }


@app.post("/chaos", status_code=200)
async def set_chaos(config: ChaosConfig) -> dict:
    """
    Configure the chaos mode.

    - mode "none"    — no chaos injected.
    - mode "latency" — all requests are delayed by latency_ms milliseconds.
    - mode "error"   — requests fail with HTTP 500 at the configured error_rate.
    """
    chaos_controller.set_mode(config.mode)

    if config.latency_ms is not None:
        chaos_controller.set_latency_ms(config.latency_ms)

    if config.error_rate is not None:
        chaos_controller.set_error_rate(config.error_rate)

    return {
        "message": f"Chaos mode set to '{config.mode}'",
        "state": chaos_controller.get_state(),
    }


@app.delete("/chaos", status_code=200)
async def reset_chaos() -> dict:
    """Reset chaos configuration to defaults (mode: none)."""
    chaos_controller.reset()
    return {
        "message": "Chaos configuration reset to defaults",
        "state": chaos_controller.get_state(),
    }


@app.post("/circuit-breaker", status_code=200)
async def toggle_circuit_breaker(config: CircuitBreakerConfig) -> dict:
    """
    Enable or disable the circuit breaker feature flag.

    When enabled, the /payment endpoint immediately returns HTTP 503
    ("Payment System Busy") regardless of the active chaos mode,
    preventing cascading failures.
    """
    chaos_controller.set_circuit_breaker_enabled(config.enabled)
    status = "enabled" if config.enabled else "disabled"
    return {
        "message": f"Circuit breaker {status}",
        "circuit_breaker_enabled": chaos_controller.get_circuit_breaker_enabled(),
    }


@app.post("/circuit-breaker/open", status_code=200)
async def open_circuit_breaker() -> dict:
    """Open the circuit breaker (triggered by self-healing automation)."""
    chaos_controller.set_circuit_breaker_enabled(True)
    return {
        "message": "Circuit breaker opened",
        "circuit_breaker_enabled": True,
    }


@app.post("/circuit-breaker/close", status_code=200)
async def close_circuit_breaker() -> dict:
    """Close the circuit breaker, restoring normal payment processing."""
    chaos_controller.set_circuit_breaker_enabled(False)
    return {
        "message": "Circuit breaker closed",
        "circuit_breaker_enabled": False,
    }


@app.get("/status")
async def get_status() -> dict:
    """Return the full chaos state including circuit breaker status."""
    return {
        "service": "payment-service",
        **chaos_controller.get_state(),
    }

"""
Unit tests for app.chaos.ChaosController.

These tests exercise the controller class directly without going through the
FastAPI HTTP layer, so they run synchronously and do not require anyio.
"""

import pytest
import asyncio

from app.chaos import ChaosController


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def controller():
    """Return a fresh ChaosController for each test."""
    return ChaosController()


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------


def test_default_mode_is_none(controller):
    assert controller.get_mode() == "none"


def test_default_circuit_breaker_is_disabled(controller):
    assert controller.get_circuit_breaker_enabled() is False


def test_default_latency_ms(controller):
    # Default is 3000 ms as defined in __init__
    assert controller.get_latency_ms() == 3000


def test_default_error_rate(controller):
    # Default is 0.5
    assert controller.get_error_rate() == 0.5


def test_get_state_returns_all_fields(controller):
    state = controller.get_state()
    assert "mode" in state
    assert "latency_ms" in state
    assert "error_rate" in state
    assert "circuit_breaker_enabled" in state


def test_get_state_default_values(controller):
    state = controller.get_state()
    assert state["mode"] == "none"
    assert state["circuit_breaker_enabled"] is False
    assert state["latency_ms"] == 3000
    assert state["error_rate"] == 0.5


# ---------------------------------------------------------------------------
# set_mode
# ---------------------------------------------------------------------------


def test_set_mode_latency(controller):
    controller.set_mode("latency")
    assert controller.get_mode() == "latency"


def test_set_mode_error(controller):
    controller.set_mode("error")
    assert controller.get_mode() == "error"


def test_set_mode_none(controller):
    controller.set_mode("latency")
    controller.set_mode("none")
    assert controller.get_mode() == "none"


def test_set_mode_invalid_raises(controller):
    with pytest.raises(ValueError):
        controller.set_mode("explode")


def test_set_mode_reflected_in_get_state(controller):
    controller.set_mode("error")
    assert controller.get_state()["mode"] == "error"


# ---------------------------------------------------------------------------
# set_latency_ms
# ---------------------------------------------------------------------------


def test_set_latency_ms_valid(controller):
    controller.set_latency_ms(500)
    assert controller.get_latency_ms() == 500


def test_set_latency_ms_zero_is_valid(controller):
    controller.set_latency_ms(0)
    assert controller.get_latency_ms() == 0


def test_set_latency_ms_negative_raises(controller):
    with pytest.raises(ValueError):
        controller.set_latency_ms(-1)


def test_set_latency_ms_reflected_in_state(controller):
    controller.set_latency_ms(200)
    assert controller.get_state()["latency_ms"] == 200


# ---------------------------------------------------------------------------
# set_error_rate
# ---------------------------------------------------------------------------


def test_set_error_rate_valid(controller):
    controller.set_error_rate(0.8)
    assert controller.get_error_rate() == 0.8


def test_set_error_rate_zero(controller):
    controller.set_error_rate(0.0)
    assert controller.get_error_rate() == 0.0


def test_set_error_rate_one(controller):
    controller.set_error_rate(1.0)
    assert controller.get_error_rate() == 1.0


def test_set_error_rate_negative_raises(controller):
    with pytest.raises(ValueError):
        controller.set_error_rate(-0.1)


def test_set_error_rate_above_one_raises(controller):
    with pytest.raises(ValueError):
        controller.set_error_rate(1.1)


# ---------------------------------------------------------------------------
# set_circuit_breaker_enabled
# ---------------------------------------------------------------------------


def test_set_circuit_breaker_true(controller):
    controller.set_circuit_breaker_enabled(True)
    assert controller.get_circuit_breaker_enabled() is True


def test_set_circuit_breaker_false(controller):
    controller.set_circuit_breaker_enabled(True)
    controller.set_circuit_breaker_enabled(False)
    assert controller.get_circuit_breaker_enabled() is False


def test_circuit_breaker_reflected_in_state(controller):
    controller.set_circuit_breaker_enabled(True)
    assert controller.get_state()["circuit_breaker_enabled"] is True


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_clears_mode(controller):
    controller.set_mode("error")
    controller.reset()
    assert controller.get_mode() == "none"


def test_reset_clears_circuit_breaker(controller):
    controller.set_circuit_breaker_enabled(True)
    controller.reset()
    assert controller.get_circuit_breaker_enabled() is False


def test_reset_restores_default_latency_ms(controller):
    controller.set_latency_ms(100)
    controller.reset()
    assert controller.get_latency_ms() == 3000


def test_reset_restores_default_error_rate(controller):
    controller.set_error_rate(0.9)
    controller.reset()
    assert controller.get_error_rate() == 0.5


def test_reset_state_matches_defaults(controller):
    controller.set_mode("latency")
    controller.set_latency_ms(1000)
    controller.set_error_rate(0.9)
    controller.set_circuit_breaker_enabled(True)
    controller.reset()
    state = controller.get_state()
    assert state["mode"] == "none"
    assert state["circuit_breaker_enabled"] is False
    assert state["latency_ms"] == 3000
    assert state["error_rate"] == 0.5


# ---------------------------------------------------------------------------
# apply_chaos — circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_chaos_raises_503_when_circuit_breaker_open(controller):
    from fastapi import HTTPException

    controller.set_circuit_breaker_enabled(True)
    with pytest.raises(HTTPException) as exc_info:
        await controller.apply_chaos()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_apply_chaos_no_exception_in_none_mode(controller):
    """mode=none with circuit breaker off should complete without raising."""
    await controller.apply_chaos()  # Should not raise


# ---------------------------------------------------------------------------
# apply_chaos — error mode with error_rate=1.0 (always fails)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_chaos_raises_500_in_error_mode_at_full_rate(controller):
    from fastapi import HTTPException

    controller.set_mode("error")
    controller.set_error_rate(1.0)  # always fails
    with pytest.raises(HTTPException) as exc_info:
        await controller.apply_chaos()
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# apply_chaos — latency mode (very short delay, just verify no exception)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_chaos_latency_mode_completes(controller):
    """Latency mode with 0 ms should complete without exception."""
    controller.set_mode("latency")
    controller.set_latency_ms(0)
    await controller.apply_chaos()  # Should not raise


# ---------------------------------------------------------------------------
# apply_chaos — error mode with error_rate=0.0 (never fails)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_chaos_error_mode_zero_rate_never_raises(controller):
    controller.set_mode("error")
    controller.set_error_rate(0.0)  # never fails
    # Run multiple times to be sure
    for _ in range(10):
        await controller.apply_chaos()  # Should never raise

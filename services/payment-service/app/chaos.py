import asyncio
import random
from typing import Literal

from fastapi import HTTPException


ChaosMode = Literal["none", "latency", "error"]


class ChaosController:
    """
    Singleton-style controller that tracks the current chaos configuration and
    applies the selected failure mode to any awaiting caller.
    """

    def __init__(self) -> None:
        self.mode: ChaosMode = "none"
        self.latency_ms: int = 3000
        self.error_rate: float = 0.5
        self.circuit_breaker_enabled: bool = False

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------

    def get_mode(self) -> ChaosMode:
        return self.mode

    def get_latency_ms(self) -> int:
        return self.latency_ms

    def get_error_rate(self) -> float:
        return self.error_rate

    def get_circuit_breaker_enabled(self) -> bool:
        return self.circuit_breaker_enabled

    def get_state(self) -> dict:
        return {
            "mode": self.mode,
            "latency_ms": self.latency_ms,
            "error_rate": self.error_rate,
            "circuit_breaker_enabled": self.circuit_breaker_enabled,
        }

    # ------------------------------------------------------------------
    # Setters
    # ------------------------------------------------------------------

    def set_mode(self, mode: ChaosMode) -> None:
        if mode not in ("none", "latency", "error"):
            raise ValueError(f"Invalid chaos mode: {mode!r}")
        self.mode = mode

    def set_latency_ms(self, latency_ms: int) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        self.latency_ms = latency_ms

    def set_error_rate(self, error_rate: float) -> None:
        if not (0.0 <= error_rate <= 1.0):
            raise ValueError("error_rate must be between 0.0 and 1.0")
        self.error_rate = error_rate

    def set_circuit_breaker_enabled(self, enabled: bool) -> None:
        self.circuit_breaker_enabled = enabled

    def reset(self) -> None:
        """Restore all chaos settings to their defaults."""
        self.mode = "none"
        self.latency_ms = 3000
        self.error_rate = 0.5
        self.circuit_breaker_enabled = False

    # ------------------------------------------------------------------
    # Chaos application
    # ------------------------------------------------------------------

    async def apply_chaos(self) -> None:
        """
        Apply the currently configured chaos behaviour to the caller.

        Decision tree:
        1. If circuit_breaker_enabled is True, raise HTTP 503 immediately
           (the circuit breaker protects downstream services from chaos).
        2. If mode == "latency", sleep for latency_ms milliseconds.
        3. If mode == "error", raise HTTP 500 with probability error_rate.
        4. If mode == "none", return immediately (no-op).
        """
        if self.circuit_breaker_enabled:
            raise HTTPException(
                status_code=503,
                detail="Payment System Busy — circuit breaker is open",
            )

        if self.mode == "latency":
            await asyncio.sleep(self.latency_ms / 1000)

        elif self.mode == "error":
            if random.random() < self.error_rate:
                raise HTTPException(
                    status_code=500,
                    detail="Simulated payment processing error (chaos mode: error)",
                )


# Module-level singleton shared across the FastAPI application.
chaos_controller = ChaosController()

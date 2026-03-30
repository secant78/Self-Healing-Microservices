"""
Self-Healing Azure Function: RestartContainer

Triggered by Azure Monitor action group webhooks when an alert fires.
Parses the alert payload to identify the affected Container App, then
takes a remediation action: either restart the container app revision
or toggle the payment-service circuit breaker.

Actions:
  - RESTART  : Restarts the active revision of the targeted Container App.
  - CIRCUIT_BREAKER_OPEN  : Calls POST /circuit-breaker/open on payment-service.
  - CIRCUIT_BREAKER_CLOSE : Calls POST /circuit-breaker/close on payment-service.

All actions are logged with correlation IDs from the alert payload so they
can be traced back in Application Insights alongside the original alert.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

import azure.functions as func
import httpx
from azure.identity import DefaultAzureCredential
from azure.mgmt.appcontainers import ContainerAppsAPIClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment configuration ────────────────────────────────────────────────
SUBSCRIPTION_ID       = os.environ["AZURE_SUBSCRIPTION_ID"]
RESOURCE_GROUP        = os.environ["RESOURCE_GROUP_NAME"]
PAYMENT_SERVICE_URL   = os.environ.get("PAYMENT_SERVICE_URL", "")

# Maps Azure Monitor alert signal names / conditions to remediation actions.
# Extend this map to add new self-healing behaviors without code changes.
ALERT_REMEDIATION_MAP: dict[str, str] = {
    "HighErrorRate":          "RESTART",
    "P95LatencyBreach":       "RESTART",
    "PaymentServiceDown":     "CIRCUIT_BREAKER_OPEN",
    "PaymentServiceRecovered":"CIRCUIT_BREAKER_CLOSE",
    "ContainerCrashLoop":     "RESTART",
    "MemoryPressure":         "RESTART",
}


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger entry point. Receives Azure Monitor common alert schema payload.
    Reference: https://learn.microsoft.com/azure/azure-monitor/alerts/alerts-common-schema
    """
    invocation_id = str(uuid.uuid4())
    received_at   = datetime.now(timezone.utc).isoformat()

    logger.info(
        "SELF_HEALING_FUNCTION_INVOKED | invocation_id=%s | received_at=%s",
        invocation_id, received_at
    )

    # ── Parse request body ────────────────────────────────────────────────────
    try:
        body = req.get_body().decode("utf-8")
        if not body:
            return _error_response(400, "Empty request body.", invocation_id)
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("Failed to parse request body: %s", exc)
        return _error_response(400, f"Invalid JSON payload: {exc}", invocation_id)

    # ── Extract alert metadata ────────────────────────────────────────────────
    try:
        alert_context  = payload.get("data", {}).get("alertContext", {})
        essentials     = payload.get("data", {}).get("essentials", {})
        alert_name     = essentials.get("alertRule", "UnknownAlert")
        alert_id       = essentials.get("alertId", invocation_id)
        fired_datetime = essentials.get("firedDateTime", received_at)
        severity       = essentials.get("severity", "Unknown")
        monitor_condition = essentials.get("monitorCondition", "Fired")

        # The target resource is typically the Container App resource URI.
        affected_resources = essentials.get("configurationItems", [])
        target_resource_uri = (
            affected_resources[0] if affected_resources else ""
        )

        # Derive Container App name from resource URI or custom dimension.
        container_app_name = _extract_container_app_name(
            target_resource_uri, alert_context, essentials
        )

        correlation_id = alert_context.get("correlationId", invocation_id)

    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Failed to extract alert metadata: %s", exc)
        return _error_response(400, f"Malformed alert payload: {exc}", invocation_id)

    logger.info(
        "ALERT_RECEIVED | alert_name=%s | alert_id=%s | severity=%s | "
        "monitor_condition=%s | container_app=%s | correlation_id=%s",
        alert_name, alert_id, severity, monitor_condition,
        container_app_name, correlation_id
    )

    # ── Skip resolved alerts ──────────────────────────────────────────────────
    if monitor_condition == "Resolved":
        logger.info(
            "ALERT_RESOLVED | alert_name=%s | no action taken | correlation_id=%s",
            alert_name, correlation_id
        )
        return _ok_response(
            action="NO_ACTION",
            reason="Alert is in Resolved state; no remediation needed.",
            alert_name=alert_name,
            correlation_id=correlation_id,
            invocation_id=invocation_id,
        )

    # ── Determine remediation action ──────────────────────────────────────────
    action = ALERT_REMEDIATION_MAP.get(alert_name)
    if not action:
        # Fallback: if the alert name contains keywords, infer an action.
        action = _infer_action_from_alert_name(alert_name)

    if not action:
        logger.warning(
            "NO_REMEDIATION_MAPPED | alert_name=%s | correlation_id=%s",
            alert_name, correlation_id
        )
        return _ok_response(
            action="NO_ACTION",
            reason=f"No remediation action mapped for alert '{alert_name}'.",
            alert_name=alert_name,
            correlation_id=correlation_id,
            invocation_id=invocation_id,
        )

    logger.info(
        "REMEDIATION_SELECTED | action=%s | alert_name=%s | target=%s | correlation_id=%s",
        action, alert_name, container_app_name, correlation_id
    )

    # ── Execute remediation ───────────────────────────────────────────────────
    try:
        if action == "RESTART":
            result = _restart_container_app(
                container_app_name, correlation_id, invocation_id
            )
        elif action == "CIRCUIT_BREAKER_OPEN":
            result = _toggle_circuit_breaker(
                "open", correlation_id, invocation_id
            )
        elif action == "CIRCUIT_BREAKER_CLOSE":
            result = _toggle_circuit_breaker(
                "close", correlation_id, invocation_id
            )
        else:
            result = {"status": "UNKNOWN_ACTION", "detail": f"Action '{action}' not implemented."}

    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "REMEDIATION_FAILED | action=%s | target=%s | correlation_id=%s | error=%s",
            action, container_app_name, correlation_id, exc
        )
        return _error_response(
            500,
            f"Remediation action '{action}' failed: {exc}",
            invocation_id,
        )

    logger.info(
        "SELF_HEALING_ACTION | action=%s | target_service=%s | alert_name=%s | "
        "correlation_id=%s | outcome=%s | invocation_id=%s",
        action, container_app_name, alert_name,
        correlation_id, result.get("status"), invocation_id
    )

    return _ok_response(
        action=action,
        reason=result.get("detail", ""),
        alert_name=alert_name,
        correlation_id=correlation_id,
        invocation_id=invocation_id,
        extra=result,
    )


# ── Remediation helpers ───────────────────────────────────────────────────────

def _restart_container_app(
    container_app_name: str,
    correlation_id: str,
    invocation_id: str,
) -> dict:
    """
    Restarts all replicas of the active revision of the given Container App
    by updating the revision-suffix, which forces a rolling restart.
    Uses DefaultAzureCredential (Managed Identity in production).
    """
    if not container_app_name:
        raise ValueError("container_app_name is empty; cannot determine restart target.")

    logger.info(
        "RESTARTING_CONTAINER_APP | app=%s | resource_group=%s | correlation_id=%s",
        container_app_name, RESOURCE_GROUP, correlation_id
    )

    credential = DefaultAzureCredential()
    client     = ContainerAppsAPIClient(credential, SUBSCRIPTION_ID)

    # Fetch the current app to get its active revision name.
    app = client.container_apps.get(RESOURCE_GROUP, container_app_name)
    active_revision_name = app.latest_revision_name

    logger.info(
        "ACTIVE_REVISION | app=%s | revision=%s | correlation_id=%s",
        container_app_name, active_revision_name, correlation_id
    )

    # Restart the revision (available in API version 2023-05-01+).
    client.container_apps_revisions.restart_revision(
        RESOURCE_GROUP, container_app_name, active_revision_name
    )

    detail = (
        f"Restarted revision '{active_revision_name}' of Container App "
        f"'{container_app_name}' in resource group '{RESOURCE_GROUP}'."
    )
    logger.info(
        "RESTART_COMPLETE | app=%s | revision=%s | correlation_id=%s",
        container_app_name, active_revision_name, correlation_id
    )
    return {"status": "RESTARTED", "detail": detail, "revision": active_revision_name}


def _toggle_circuit_breaker(
    state: str,  # "open" or "close"
    correlation_id: str,
    invocation_id: str,
) -> dict:
    """
    Calls the circuit breaker management endpoint on the payment service.
    The payment service exposes:
      POST /circuit-breaker/open   — enables chaos / opens circuit
      POST /circuit-breaker/close  — disables chaos / closes circuit
    """
    if not PAYMENT_SERVICE_URL:
        raise ValueError(
            "PAYMENT_SERVICE_URL environment variable is not set. "
            "Cannot toggle circuit breaker."
        )

    endpoint = f"{PAYMENT_SERVICE_URL.rstrip('/')}/circuit-breaker/{state}"
    headers  = {
        "Content-Type": "application/json",
        "X-Correlation-Id": correlation_id,
        "X-Invocation-Id": invocation_id,
        "X-Source": "self-healing-function",
    }
    body = {
        "triggered_by": "self-healing-function",
        "correlation_id": correlation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "CIRCUIT_BREAKER_TOGGLE | state=%s | endpoint=%s | correlation_id=%s",
        state, endpoint, correlation_id
    )

    with httpx.Client(timeout=10.0) as http_client:
        response = http_client.post(endpoint, json=body, headers=headers)
        response.raise_for_status()
        response_body = response.json() if response.content else {}

    action_label = "CIRCUIT_BREAKER_OPENED" if state == "open" else "CIRCUIT_BREAKER_CLOSED"
    detail = (
        f"Circuit breaker {state}ed on payment-service. "
        f"HTTP {response.status_code}. Response: {response_body}"
    )
    logger.info(
        "%s | endpoint=%s | http_status=%d | correlation_id=%s",
        action_label, endpoint, response.status_code, correlation_id
    )
    return {
        "status": action_label,
        "detail": detail,
        "http_status": response.status_code,
        "response": response_body,
    }


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _extract_container_app_name(
    resource_uri: str,
    alert_context: dict,
    essentials: dict,
) -> str:
    """
    Attempts to determine the Container App name from:
    1. The resource URI path segment (e.g., .../containerApps/my-app)
    2. A custom dimension in the alert context.
    3. The alert target name from essentials.
    """
    if resource_uri:
        parts = resource_uri.split("/")
        try:
            idx = [p.lower() for p in parts].index("containerapps")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        except ValueError:
            pass

    # Custom dimension set by the KQL alert query.
    custom_props = alert_context.get("customProperties", {})
    if custom_props.get("containerAppName"):
        return custom_props["containerAppName"]

    # Fall back to the alert target name from essentials.
    target_names = essentials.get("targetArmIds", [])
    if target_names:
        return target_names[0].split("/")[-1]

    return os.environ.get("DEFAULT_CONTAINER_APP_NAME", "")


def _infer_action_from_alert_name(alert_name: str) -> str | None:
    """Infer a remediation action from keywords in the alert name."""
    name_lower = alert_name.lower()
    if any(kw in name_lower for kw in ("crash", "restart", "oom", "memory", "latency", "error")):
        return "RESTART"
    if "circuit" in name_lower and "open" in name_lower:
        return "CIRCUIT_BREAKER_OPEN"
    if "circuit" in name_lower and ("close" in name_lower or "recover" in name_lower):
        return "CIRCUIT_BREAKER_CLOSE"
    return None


# ── Response builders ─────────────────────────────────────────────────────────

def _ok_response(
    action: str,
    reason: str,
    alert_name: str,
    correlation_id: str,
    invocation_id: str,
    extra: dict | None = None,
) -> func.HttpResponse:
    body = {
        "status": "OK",
        "action": action,
        "reason": reason,
        "alert_name": alert_name,
        "correlation_id": correlation_id,
        "invocation_id": invocation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        body["details"] = extra
    return func.HttpResponse(
        body=json.dumps(body, indent=2),
        status_code=200,
        mimetype="application/json",
    )


def _error_response(status_code: int, message: str, invocation_id: str) -> func.HttpResponse:
    body = {
        "status": "ERROR",
        "message": message,
        "invocation_id": invocation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return func.HttpResponse(
        body=json.dumps(body, indent=2),
        status_code=status_code,
        mimetype="application/json",
    )

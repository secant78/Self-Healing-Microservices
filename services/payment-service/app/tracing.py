import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


def configure_tracing() -> None:
    """
    Configure OpenTelemetry tracing with an OTLP HTTP exporter and,
    optionally, Azure Monitor when APPLICATIONINSIGHTS_CONNECTION_STRING is set.
    """
    resource = Resource(attributes={SERVICE_NAME: "payment-service"})

    provider = TracerProvider(resource=resource)

    # --- OTLP HTTP exporter (e.g. OpenTelemetry Collector) ---
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint.rstrip("/") + "/v1/traces",
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # --- Azure Monitor exporter ---
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if connection_string:
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

            azure_exporter = AzureMonitorTraceExporter(
                connection_string=connection_string
            )
            provider.add_span_processor(BatchSpanProcessor(azure_exporter))
        except ImportError:
            # Package not installed; skip silently so the app still starts.
            pass

    trace.set_tracer_provider(provider)

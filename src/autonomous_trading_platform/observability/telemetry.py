from __future__ import annotations

import atexit
import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_telemetry(service_name: str) -> None:
    grpc_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    http_logs_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "http://localhost:4318/v1/logs",
    )

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "autonomous_trading_platform",
            "deployment.environment": os.getenv("APP_ENV", "dev"),
        }
    )

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=grpc_endpoint, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    # Metrics
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=grpc_endpoint, insecure=True),
        export_interval_millis=5000,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    # Logs
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=http_logs_endpoint))
    )
    set_logger_provider(logger_provider)

    root_logger = logging.getLogger()
    if not any(isinstance(h, LoggingHandler) for h in root_logger.handlers):
        root_logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))

    # Short-lived CLI/backtest processes can exit before the batch processors'
    # background threads get a scheduled export cycle — flush explicitly on exit
    # so traces/metrics/logs from the process's final seconds aren't dropped.
    atexit.register(tracer_provider.shutdown)
    atexit.register(meter_provider.shutdown)
    atexit.register(logger_provider.shutdown)

from prometheus_client import Counter, Histogram, start_http_server

MESSAGES_PROCESSED = Counter("worker_messages_processed_total", "Mensajes procesados OK", ["consumer"])
MESSAGES_FAILED = Counter("worker_messages_failed_total", "Mensajes fallidos", ["consumer", "kind"])
MESSAGES_RETRIED = Counter("worker_messages_retried_total", "Mensajes enviados a retry", ["consumer"])
MESSAGES_DLQ = Counter("worker_messages_dlq_total", "Mensajes enviados a DLQ", ["consumer"])
MESSAGES_DUPLICATED = Counter("worker_messages_duplicated_total", "Mensajes duplicados ignorados", ["consumer"])
PROCESSING_SECONDS = Histogram("worker_processing_seconds", "Tiempo de procesamiento", ["consumer"])


def start_metrics_server(port: int = 9100) -> None:
    start_http_server(port)

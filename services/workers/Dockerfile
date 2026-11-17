FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY services/workers/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY libs/wcommon /tmp/wcommon
RUN pip install /tmp/wcommon && rm -rf /tmp/wcommon /tmp/requirements.txt

COPY services/workers/workers ./workers

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 9100
CMD ["python", "-m", "workers.transfer_orchestrator"]

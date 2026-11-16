FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/wcommon /tmp/wcommon
COPY services/warehouse-api/requirements.txt requirements.txt
RUN pip install /tmp/wcommon -r requirements.txt && rm -rf /tmp/wcommon

COPY services/warehouse-api/app ./app
COPY services/warehouse-api/main.py main.py

RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8000

# revisa que la api responda
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

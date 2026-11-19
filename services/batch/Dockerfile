FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY services/batch/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/batch/batch ./batch
RUN useradd -r -u 10001 app
USER app
CMD ["python", "-m", "batch.daily_report"]

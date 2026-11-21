FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY libs/wcommon ./libs/wcommon
COPY services/iot/requirements.txt .
RUN pip install --no-cache-dir ./libs/wcommon -r requirements.txt
COPY services/iot/iot ./iot
RUN useradd -r -u 10001 app
USER app
EXPOSE 9100
CMD ["python", "-m", "iot.simulator"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static

RUN mkdir -p /app/data
ENV SEAL_DATA_FILE=/app/data/state.json

EXPOSE 8000

CMD ["python", "-m", "flask", "--app", "app.web:create_app", "run", "--host", "0.0.0.0", "--port", "8000"]

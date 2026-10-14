FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini .

ENV PYTHONUNBUFFERED=1
EXPOSE 8028

# Schema is owned by Alembic: migrate, then serve.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn src.main:app --host 0.0.0.0 --port 8028"]

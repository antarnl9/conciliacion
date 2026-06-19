FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Credenciales de ClickHouse vía variables de entorno (CH_HOST, CH_USER, CH_PASSWORD, ...)
# DuckDB persiste en /app/data — montar un volumen ahí en el host (Railway/Render/Fly).
ENV PORT=8770
EXPOSE 8770
CMD ["sh", "-c", "uvicorn conciliacion.api.main:app --host 0.0.0.0 --port ${PORT:-8770}"]

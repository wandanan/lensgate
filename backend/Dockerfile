FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code under backend/ so imports (backend.src.xxx) resolve
COPY . ./backend/

# Create writable directory for valuation logs
RUN mkdir -p /app/valuation && chmod 777 /app/valuation

ENV HOST=0.0.0.0
ENV PORT=9856
ENV PYTHONUNBUFFERED=1

EXPOSE 9856

CMD ["uvicorn", "backend.src.main:app", "--host", "0.0.0.0", "--port", "9856"]

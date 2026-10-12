# Small Python image for sidecar service
FROM python:3.11-slim

WORKDIR /app

# PYTHONPATH: the coc wrapper runs with /work (the user's folder) as its working directory
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sidecar/ sidecar/
COPY tools/ tools/
COPY verifier/ verifier/
COPY coc/ coc/

CMD ["python", "-m", "sidecar.main", "--config", "config.toml"]

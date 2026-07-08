FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Roda como usuario nao-root: o container processa arquivos enviados por
# usuarios (PDF/imagem via poppler/Tesseract/OpenCV) — limita o estrago de
# uma eventual vulnerabilidade nessas libs. data/ gravavel p/ SQLite local.
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser
# Daqui em diante tudo roda sem privilegios de root.

CMD ["python", "main.py"]

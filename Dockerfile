FROM python:3.11-slim

WORKDIR /app

# System deps for OpenCV and EasyOCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download EasyOCR models (ru+en) so first user photo doesn't trigger download + OOM
RUN python -c "import easyocr; easyocr.Reader(('ru', 'en'), gpu=False, verbose=True)"

COPY . .

CMD ["python", "-m", "main"]

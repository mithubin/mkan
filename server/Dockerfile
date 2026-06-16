FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends antiword && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/db /app/data/uploads

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# ⬇️ fix CRLF + make executable
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh
CMD ["./start.sh"]
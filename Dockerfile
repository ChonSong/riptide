FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY riptide/ riptide/
COPY server.py .

# Runtime config
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8477

EXPOSE 8477

CMD ["python", "server.py"]

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Extract backend code from tarball chunks
COPY chunks/ /tmp/chunks/
RUN cat /tmp/chunks/chunk_*.txt | base64 -d | tar xzf - -C /app && rm -rf /tmp/chunks

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

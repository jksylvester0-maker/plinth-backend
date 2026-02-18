FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY chunks/ /tmp/chunks/
RUN cat /tmp/chunks/chunk_*.txt | base64 -d | tar xzf - -C /app && rm -rf /tmp/chunks
COPY main_override.py /app/main.py
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

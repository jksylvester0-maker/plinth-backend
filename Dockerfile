FROM python:3.11-slim
WORKDIR /app

# Install dependencies (no passlib/bcrypt needed)
RUN pip install --no-cache-dir fastapi uvicorn python-jose[cryptography] python-dotenv

# Copy the single-file backend
COPY main_override.py /app/main.py

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

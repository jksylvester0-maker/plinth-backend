FROM python:3.11-slim
WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn python-jose[cryptography] passlib[bcrypt] python-dotenv

# Copy the single-file backend
COPY main_override.py /app/main.py

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

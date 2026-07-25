FROM python:3.11-slim

WORKDIR /app

# Copy pinned dependencies first (better build caching)
COPY requirements.txt /app/requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . /app

# Install your package (src layout)
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "autonomous_trading_platform.interfaces.rest.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

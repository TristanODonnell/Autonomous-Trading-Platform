FROM python:3.11-slim

WORKDIR /app

# Copy pinned dependencies first (better build caching)
COPY src/requirements.txt /app/requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . /app

# Install your package (src layout)
RUN pip install --no-cache-dir -e .

CMD ["python", "-c", "print('container ok')"]

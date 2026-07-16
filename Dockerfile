FROM python:3.11-slim
WORKDIR /app

# Install small essentials (no heavy build tools needed with psycopg2-binary)
RUN python -m pip install --upgrade pip
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
ENV PYTHONUNBUFFERED=1
ENV FEATURE_EXPORT_DIR=/exports/feature_exports
RUN mkdir -p /exports
VOLUME ["/exports"]
CMD ["python", "/app/patient_csv_generator.py"]

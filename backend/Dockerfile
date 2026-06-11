FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libffi-dev build-essential default-mysql-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . /app/backend

EXPOSE 5000

# --forwarded-allow-ips '*' = trust Cloud Run's proxy so X-Forwarded-Proto is honored
# and request.base_url is https (not http). Without it, the .ics calendar link was built
# as http:// and the https app's fetch was blocked as mixed content ("Failed to fetch").
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000", "--forwarded-allow-ips", "*"]

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir google-adk==1.23.0 aiomysql

COPY cs_navigator_unified/ ./cs_navigator_unified/

EXPOSE 8080

CMD ["sh", "-c", "if [ -n \"$ADK_SESSION_DB_URI\" ]; then python -m google.adk.cli web . --port 8080 --host 0.0.0.0 --session_service_uri $ADK_SESSION_DB_URI; else python -m google.adk.cli web . --port 8080 --host 0.0.0.0; fi"]

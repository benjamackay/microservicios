FROM python:3.12-slim

WORKDIR /app
COPY peers/summaries-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --no-create-home app
COPY --chown=10001:10001 peers/summaries-service/app ./app
COPY --chown=10001:10001 peers/summaries-service/gateway ./gateway
COPY --chown=10001:10001 api-gateway/main.py ./gateway_entry.py
COPY --chown=10001:10001 peers/summaries-service/web ./web

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "gateway_entry:app", "--host", "0.0.0.0", "--port", "8000"]

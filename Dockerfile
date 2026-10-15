# PlantGuard backend: ONE image for every role (PLANTGUARD_ROLE = api | engine | worker | migrate | all).
#   docker build -t plantguard-api .
#   Cloud Run service:  (default CMD) uvicorn on $PORT          Cloud Run job:  python -m app.ops migrate
# ---------------------------------------------------------------- build stage: wheels
FROM python:3.12-slim AS build
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------- runtime stage
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv:/srv/p1-backend:/srv/p2-agents \
    DATA_MODE=sim LOG_FORMAT=json PORT=8000
COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels \
 && useradd -r -u 10001 -d /srv plantguard
COPY plantguard_contracts plantguard_contracts
COPY p1-backend/app p1-backend/app
COPY p2-agents p2-agents
COPY fixtures fixtures
COPY config config
COPY p4-ml-data/corpus p4-ml-data/corpus
# single-process mode keeps SQLite + FAISS in [storage] data_dir (var/plantguard); production uses Cloud SQL + Qdrant
RUN mkdir -p /srv/var/plantguard && chown -R plantguard /srv/var && python -m compileall -q p1-backend/app p2-agents
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import os,urllib.request as u; u.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz')"
# exec: uvicorn gets SIGTERM directly (Cloud Run gives 10 s to finish; in-flight runs are re-queued by the worker lease)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75 --timeout-graceful-shutdown 8 --no-server-header --no-access-log"]

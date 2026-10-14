FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/srv:/srv/p1-backend:/srv/p2-agents
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY plantguard_contracts plantguard_contracts
COPY p1-backend p1-backend
COPY p2-agents p2-agents
COPY fixtures fixtures
COPY config config
COPY p4-ml-data p4-ml-data
# sim mode keeps its databases + FAISS index in [storage] data_dir (var/plantguard); mount a volume here to persist them
RUN useradd -r -u 10001 plantguard && mkdir -p /srv/var/plantguard && chown -R plantguard /srv/var
USER plantguard
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/auth/config')"
# Cloud Run sets $PORT
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

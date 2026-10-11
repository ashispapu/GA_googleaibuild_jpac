FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/srv:/srv/p1-backend
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY plantguard_contracts plantguard_contracts
COPY p1-backend p1-backend
COPY fixtures fixtures
COPY config config
RUN useradd -r -u 10001 plantguard
USER plantguard
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/auth/config')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

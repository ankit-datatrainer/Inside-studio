# Insight Studio - production image (works on AWS, any VPS, local Docker)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PORT=8000 \
    WORKSPACE_DIR=/data/workspaces \
    WORKSPACE_TTL_HOURS=48

WORKDIR /app

# matplotlib needs these system libs for PNG/PDF export
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreetype6 libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/workspaces && \
    useradd --create-home --uid 10001 studio && \
    chown -R studio:studio /app /data
USER studio

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "--timeout", "180", "--access-logfile", "-", "wsgi:app"]

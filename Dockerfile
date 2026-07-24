# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS web

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY pyproject.toml README.md ./
COPY klean_pod_checker ./klean_pod_checker

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /app/proofs /app/outputs \
    && chown -R app:app /app/proofs /app/outputs

USER app

EXPOSE 8092

CMD ["gunicorn", "--workers", "2", "--threads", "2", "--timeout", "90", "--bind", "0.0.0.0:8092", "--access-logfile", "-", "--error-logfile", "-", "klean_pod_checker.wsgi:app"]


FROM web AS automation

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY package.json package-lock.json ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm unzip tzdata \
    && npm ci --omit=dev \
    && npx playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

COPY docker/run-shopee-scheduler.sh /usr/local/bin/run-shopee-scheduler

RUN chmod 755 /usr/local/bin/run-shopee-scheduler \
    && mkdir -p /runtime/shopee/session /runtime/shopee/reports /runtime/shopee/work \
    && chown -R app:app /runtime

USER app

CMD ["/usr/local/bin/run-shopee-scheduler"]

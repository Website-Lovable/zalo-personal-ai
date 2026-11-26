# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 1000 app

COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=true
# Optional build secret `pip_ca` (corporate proxy CA bundle). Not stored in the image.
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; \
    else pip install -r requirements.txt; fi

COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]

# syntax=docker/dockerfile:1
# One image for web, worker and beat. Dev (docker compose up) installs dev tools;
# production builds pass --build-arg INSTALL_DEV=false (see .github/workflows/release.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 1000 app && chown app:app /app

COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=true
# Optional build secret `pip_ca` (corporate proxy CA bundle). Not stored in the image.
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; \
    else pip install -r requirements.txt; fi

COPY --chown=app:app . .
USER app

ARG GIT_SHA=dev
ENV APP_VERSION=$GIT_SHA
LABEL org.opencontainers.image.source="https://github.com/Website-Lovable/zalo-personal-ai" \
      org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8000
# Static files are served by WhiteNoise (collected at build time, no secrets needed).
RUN DJANGO_SETTINGS_MODULE=config.settings.development python manage.py collectstatic --noinput -v0
# Dev default (daphne). Production overrides: gunicorn -c deploy/gunicorn.conf.py config.asgi:application
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]

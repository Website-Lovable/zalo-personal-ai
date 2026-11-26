"""Shared settings. All secrets come from environment variables (see .env.example)."""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = env("SECRET_KEY")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "daphne",  # ASGI runserver (WebSocket notifications); must come before staticfiles
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "channels",
    # local
    "common",
    "apps.accounts",
    "apps.channels",
    "apps.contacts",
    "apps.conversations",
    "apps.ai_engine",
    "apps.memory",
    "apps.permissions",
    "apps.dashboard",
    "apps.integrations.aiva",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves the admin UI's static files
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.parse(env("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}
# PostgreSQL: a bounded connection pool per process (psycopg_pool). Under ASGI every request
# may run on a different thread, so persistent per-thread connections (CONN_MAX_AGE) pile up
# until PostgreSQL refuses clients; the Phase 5 load test hit exactly that. Size the pool so
# (web processes + worker processes) * DB_POOL_MAX_SIZE stays below max_connections.
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql" and env_bool("DB_POOL", True):
    DATABASES["default"]["CONN_MAX_AGE"] = 0
    DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
        "min_size": int(env("DB_POOL_MIN_SIZE", "2")),
        "max_size": int(env("DB_POOL_MAX_SIZE", "10")),
        "timeout": float(env("DB_POOL_TIMEOUT_SECONDS", "10")),
    }
else:
    DATABASES["default"]["CONN_MAX_AGE"] = int(env("DB_CONN_MAX_AGE", "60"))

AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- REST framework / JWT / OpenAPI -------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["apps.accounts.rbac.IsTenantMember"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.DefaultPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Scoped throttles on the public auth endpoints (Phase 5 security review, finding S1).
    # Counters live in the cache: use Redis (CACHE_BACKEND=redis) once there are several
    # processes, otherwise each process counts on its own.
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": env("THROTTLE_AUTH_LOGIN", "10/min"),
        "auth_register": env("THROTTLE_AUTH_REGISTER", "5/hour"),
        "auth_refresh": env("THROTTLE_AUTH_REFRESH", "30/min"),
    },
}
# Reverse proxies in front of the app that append to X-Forwarded-For (Caddy in production = 1).
# 0 = ignore X-Forwarded-For entirely (direct exposure, local dev).
TRUSTED_PROXY_COUNT = int(env("TRUSTED_PROXY_COUNT", "0"))
REST_FRAMEWORK["NUM_PROXIES"] = TRUSTED_PROXY_COUNT or None

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("JWT_REFRESH_DAYS", "7"))),
    "SIGNING_KEY": env("JWT_SECRET_KEY") or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Zalo Personal AI Assistant API",
    "DESCRIPTION": "Multi-tenant API (Phase 3: AI engine).",
    "VERSION": "0.2.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "ReplyPermissionEnum": "apps.contacts.models.ReplyPermission",
        "DataOriginEnum": "common.models.DataOrigin",
        "ChannelStatusEnum": "apps.channels.models.Channel.Status",
    },
}

# --- Celery / Redis / Channels ----------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
# Shared cache (throttle counters, ...). locmem is per process: fine for dev and tests only.
CACHE_BACKEND = env("CACHE_BACKEND", "locmem")
if CACHE_BACKEND == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("CACHE_REDIS_URL", REDIS_URL),
            "KEY_PREFIX": "zalo-ai",
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TIMEZONE = TIME_ZONE
# Real-time notifications. Redis is needed as soon as web and workers are separate processes.
if env("CHANNEL_LAYER", "redis") == "redis":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# --- Encryption of Zalo tokens / sessions ------------------------------------------
ENCRYPTION_KEY = env("ENCRYPTION_KEY")

# --- LLM (Anthropic via LLMProvider) ----------------------------------------------
LLM_PROVIDER = env("LLM_PROVIDER", "mock")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", "")
LLM_MODEL_MAIN = env("LLM_MODEL_MAIN", "claude-sonnet-5-5")
LLM_MODEL_FAST = env("LLM_MODEL_FAST", "claude-haiku-5-5")
LLM_TIMEOUT_SECONDS = int(env("LLM_TIMEOUT_SECONDS", "60"))
# Re-run a request declined by Anthropic's safety classifiers on a fallback model.
LLM_REFUSAL_FALLBACK = env_bool("LLM_REFUSAL_FALLBACK", True)
LLM_EFFORT_REPLY = env("LLM_EFFORT_REPLY", "medium")
LLM_EFFORT_FAST = env("LLM_EFFORT_FAST", "low")
LLM_REPLY_MAX_TOKENS = int(env("LLM_REPLY_MAX_TOKENS", "2048"))

# --- AI engine (Phase 3) --------------------------------------------------------------
# Platform-wide switch. false = no AI drafts or replies for any tenant (inbound still stored).
AI_ENGINE_ENABLED = env_bool("AI_ENGINE_ENABLED", True)
# Below this self-reported confidence an auto reply becomes a draft for approval.
AI_MIN_AUTO_CONFIDENCE = float(env("AI_MIN_AUTO_CONFIDENCE", "0.7"))
# Auto replies allowed per channel per hour / per conversation per 10 minutes.
AI_MAX_AUTO_REPLIES_PER_CHANNEL_HOUR = int(env("AI_MAX_AUTO_REPLIES_PER_CHANNEL_HOUR", "60"))
AI_MAX_AUTO_REPLIES_PER_CONVERSATION_10MIN = int(
    env("AI_MAX_AUTO_REPLIES_PER_CONVERSATION_10MIN", "5")
)
AI_MAX_REPLY_CHARS = int(env("AI_MAX_REPLY_CHARS", "1500"))
# Messages kept verbatim in the prompt (short-term memory window).
AI_SHORT_TERM_WINDOW = int(env("AI_SHORT_TERM_WINDOW", "20"))
AI_SHORT_TERM_TTL_HOURS = int(env("AI_SHORT_TERM_TTL_HOURS", "24"))
# Summarize / extract long-term facts every N new messages in a conversation.
AI_MEMORY_UPDATE_EVERY = int(env("AI_MEMORY_UPDATE_EVERY", "6"))
AI_LONG_TERM_IN_PROMPT = int(env("AI_LONG_TERM_IN_PROMPT", "10"))
# When staff reply by hand (in the app or in Zalo), the AI stays silent for this long.
HUMAN_TAKEOVER_AUTO_MINUTES = int(env("HUMAN_TAKEOVER_AUTO_MINUTES", "30"))

# --- Channels feature flags --------------------------------------------------------
ZALO_PERSONAL_UNOFFICIAL_ENABLED = env_bool("ZALO_PERSONAL_UNOFFICIAL_ENABLED", False)
# Personal Zalo (Phase 2b): kill switch for every tenant, sidecar, and send limits.
ZALO_PERSONAL_EMERGENCY_STOP = env_bool("ZALO_PERSONAL_EMERGENCY_STOP", False)
ZALO_PERSONAL_SIDECAR_URL = env("ZALO_PERSONAL_SIDECAR_URL", "http://zalo-personal:3100")
ZALO_PERSONAL_SIDECAR_SECRET = env("ZALO_PERSONAL_SIDECAR_SECRET", "")
ZALO_PERSONAL_SIGNATURE_TOLERANCE_SECONDS = int(
    env("ZALO_PERSONAL_SIGNATURE_TOLERANCE_SECONDS", "300")
)
ZALO_PERSONAL_SEND_WINDOW = env("ZALO_PERSONAL_SEND_WINDOW", "07:30-21:30")  # local time
ZALO_PERSONAL_MAX_PER_MINUTE = int(env("ZALO_PERSONAL_MAX_PER_MINUTE", "4"))
ZALO_PERSONAL_MAX_PER_HOUR = int(env("ZALO_PERSONAL_MAX_PER_HOUR", "40"))
ZALO_PERSONAL_MAX_PER_DAY = int(env("ZALO_PERSONAL_MAX_PER_DAY", "150"))
ZALO_PERSONAL_MAX_PER_CONTACT_PER_HOUR = int(env("ZALO_PERSONAL_MAX_PER_CONTACT_PER_HOUR", "10"))
ZALO_PERSONAL_MIN_GAP_SECONDS = int(env("ZALO_PERSONAL_MIN_GAP_SECONDS", "8"))
ZALO_PERSONAL_MIN_REPLY_DELAY_SECONDS = int(env("ZALO_PERSONAL_MIN_REPLY_DELAY_SECONDS", "5"))
ZALO_PERSONAL_MAX_REPLY_DELAY_SECONDS = int(env("ZALO_PERSONAL_MAX_REPLY_DELAY_SECONDS", "45"))
ZALO_PERSONAL_TYPING_CHARS_PER_SECOND = int(env("ZALO_PERSONAL_TYPING_CHARS_PER_SECOND", "6"))
ZALO_PERSONAL_REPLY_WINDOW_HOURS = int(env("ZALO_PERSONAL_REPLY_WINDOW_HOURS", "24"))
ZALO_PERSONAL_MAX_CONSECUTIVE_FAILURES = int(env("ZALO_PERSONAL_MAX_CONSECUTIVE_FAILURES", "3"))

# --- AIVA integration (off | sandbox | live) ---------------------------------------
AIVA_INTEGRATION_MODE = env("AIVA_INTEGRATION_MODE", "off")
if AIVA_INTEGRATION_MODE not in {"off", "sandbox", "live"}:
    raise ImproperlyConfigured("AIVA_INTEGRATION_MODE must be off, sandbox or live")
AIVA_BASE_URL = env("AIVA_BASE_URL", "")
AIVA_WEBHOOK_SECRET = env("AIVA_WEBHOOK_SECRET", "")
AIVA_SERVICE_TOKEN = env("AIVA_SERVICE_TOKEN", "")
AIVA_SIGNATURE_TOLERANCE_SECONDS = int(env("AIVA_SIGNATURE_TOLERANCE_SECONDS", "300"))
# Live = real customer data. Needs a separate owner approval (Kickoff 2.0 #3, Phase 6).
AIVA_LIVE_APPROVED = env_bool("AIVA_LIVE_APPROVED", False)
if AIVA_INTEGRATION_MODE == "live" and not AIVA_LIVE_APPROVED:
    raise ImproperlyConfigured(
        "AIVA_INTEGRATION_MODE=live needs owner approval: set AIVA_LIVE_APPROVED=true (Phase 6)"
    )
# Outbound (Zalo AI -> AIVA): http = signed webhooks to AIVA_BASE_URL; mock = in-process
# recorder for sandbox runs without a running AIVA (never allowed in live).
AIVA_CLIENT = env("AIVA_CLIENT", "http")
if AIVA_CLIENT not in {"http", "mock"}:
    raise ImproperlyConfigured("AIVA_CLIENT must be http or mock")
if AIVA_INTEGRATION_MODE == "live" and AIVA_CLIENT != "http":
    raise ImproperlyConfigured("AIVA_INTEGRATION_MODE=live needs AIVA_CLIENT=http")
# Reference of the director's written approval of live mode (e.g. "QĐ 12/2026 ngày 01/12").
# Written into the audit trail; the Phase 6 preflight fails without it.
AIVA_LIVE_APPROVAL_REF = env("AIVA_LIVE_APPROVAL_REF", "")
AIVA_EVENTS_PATH = env("AIVA_EVENTS_PATH", "/api/integration/zalo-ai/v1/events")
AIVA_HEALTH_PATH = env("AIVA_HEALTH_PATH", "/api/integration/zalo-ai/v1/health")
AIVA_OUTBOUND_TOKEN = env("AIVA_OUTBOUND_TOKEN", "")  # bearer token AIVA gave us (optional)
AIVA_HTTP_TIMEOUT_SECONDS = float(env("AIVA_HTTP_TIMEOUT_SECONDS", "10"))
# Inbound (AIVA -> Zalo AI): AIVA presents AIVA_SERVICE_TOKEN and signs with this secret.
AIVA_INBOUND_SECRET = env("AIVA_INBOUND_SECRET", "")
# Outbox delivery: exponential backoff base * 2^(attempt-1), capped; then dead-letter.
AIVA_OUTBOX_MAX_ATTEMPTS = int(env("AIVA_OUTBOX_MAX_ATTEMPTS", "8"))
AIVA_OUTBOX_BACKOFF_BASE_SECONDS = int(env("AIVA_OUTBOX_BACKOFF_BASE_SECONDS", "30"))
AIVA_OUTBOX_BACKOFF_MAX_SECONDS = int(env("AIVA_OUTBOX_BACKOFF_MAX_SECONDS", "3600"))
AIVA_OUTBOX_BATCH_SIZE = int(env("AIVA_OUTBOX_BATCH_SIZE", "50"))
# At most one conversation.summary per conversation per interval.
AIVA_SUMMARY_MIN_INTERVAL_MINUTES = int(env("AIVA_SUMMARY_MIN_INTERVAL_MINUTES", "60"))
# Long-term facts at or above this importance become profile.update_suggested for CRS.
AIVA_PROFILE_SUGGESTION_MIN_IMPORTANCE = int(env("AIVA_PROFILE_SUGGESTION_MIN_IMPORTANCE", "7"))

# --- App behaviour -----------------------------------------------------------------
DEFAULT_AI_RESPONSE_DELAY_SECONDS = int(env("DEFAULT_AI_RESPONSE_DELAY_SECONDS", "3"))
APPROVAL_EXPIRY_HOURS = int(env("APPROVAL_EXPIRY_HOURS", "24"))
MAX_MEMORY_PER_CONTACT = int(env("MAX_MEMORY_PER_CONTACT", "100"))

# --- Logging, error mails, monitoring (Phase 5) -------------------------------------------
# LOG_FORMAT=json -> one JSON object per line (production); text otherwise.
LOG_FORMAT = env("LOG_FORMAT", "text")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "common.logging.JSONFormatter"},
        "text": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "text",
        },
        # Unhandled errors (HTTP 500) are mailed to ADMINS when SMTP is configured.
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
            "filters": ["require_debug_false"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console", "mail_admins"], "level": "ERROR"},
        "httpx": {"level": env("HTTPX_LOG_LEVEL", "WARNING")},
    },
}
ADMINS = [("Ops", addr) for addr in env_list("ERROR_EMAIL_TO")]
SERVER_EMAIL = env("SERVER_EMAIL", "zalo-ai@localhost")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if env("EMAIL_HOST")
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_SUBJECT_PREFIX = "[Zalo AI] "
# Bearer token Prometheus presents on /metrics/. Empty = endpoint disabled (404).
METRICS_TOKEN = env("METRICS_TOKEN", "")
# Django admin lives here; use a non-obvious path in production (e.g. "quan-tri-7f3a/").
ADMIN_URL = env("ADMIN_URL", "admin/")
if not ADMIN_URL.endswith("/") or ADMIN_URL.startswith("/"):
    raise ImproperlyConfigured("ADMIN_URL must look like 'path/' (no leading slash)")

# --- Zalo channels (Phase 2) ---------------------------------------------------------
# mock = outbound calls go to the in-memory MockChannelProvider (dev/test default);
# real = official Zalo OA / Bot APIs. Webhooks are always verified with the real rules.
ZALO_CHANNEL_BACKEND = env("ZALO_CHANNEL_BACKEND", "mock")
if ZALO_CHANNEL_BACKEND not in {"mock", "real"}:
    raise ImproperlyConfigured("ZALO_CHANNEL_BACKEND must be mock or real")
# Public HTTPS base URL of this app (webhook + OAuth callback URLs are built from it).
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "")
ZALO_HTTP_TIMEOUT_SECONDS = int(env("ZALO_HTTP_TIMEOUT_SECONDS", "10"))
# Zalo OA (one Zalo app at developers.zalo.me serves every tenant's OA)
ZALO_OA_APP_ID = env("ZALO_OA_APP_ID", "")
ZALO_OA_APP_SECRET = env("ZALO_OA_APP_SECRET", "")
# "OA Secret Key" from the app's Webhook page (signs X-ZEvent-Signature).
ZALO_OA_WEBHOOK_SECRET = env("ZALO_OA_WEBHOOK_SECRET", "")
ZALO_OA_OAUTH_URL = env("ZALO_OA_OAUTH_URL", "https://oauth.zaloapp.com/v4/oa")
ZALO_OA_API_URL = env("ZALO_OA_API_URL", "https://openapi.zalo.me")
ZALO_OA_OAUTH_REDIRECT_URI = env("ZALO_OA_OAUTH_REDIRECT_URI", "")
# Optional page to redirect to after OAuth (gets ?status=&message=&channel_id=).
ZALO_OA_OAUTH_RESULT_URL = env("ZALO_OA_OAUTH_RESULT_URL", "")
ZALO_OA_OAUTH_STATE_TTL_SECONDS = int(env("ZALO_OA_OAUTH_STATE_TTL_SECONDS", "600"))
ZALO_OA_DEFAULT_TOKEN_TTL_SECONDS = int(env("ZALO_OA_DEFAULT_TOKEN_TTL_SECONDS", "90000"))
ZALO_OA_TOKEN_REFRESH_MARGIN_SECONDS = int(env("ZALO_OA_TOKEN_REFRESH_MARGIN_SECONDS", "3600"))
ZALO_OA_SYNC_PAGE_SIZE = int(env("ZALO_OA_SYNC_PAGE_SIZE", "50"))
ZALO_OA_SYNC_MAX_CONTACTS = int(env("ZALO_OA_SYNC_MAX_CONTACTS", "5000"))
# Domain verification file /zalo_verifier<code>.html (Zalo developer console).
ZALO_SITE_VERIFICATION_CODE = env("ZALO_SITE_VERIFICATION_CODE", "")
# Zalo Bot Platform
ZALO_BOT_API_URL = env("ZALO_BOT_API_URL", "https://bot-api.zaloplatforms.com")
ZALO_BOT_POLL_TIMEOUT_SECONDS = int(env("ZALO_BOT_POLL_TIMEOUT_SECONDS", "10"))
ZALO_BOT_POLL_INTERVAL_SECONDS = int(env("ZALO_BOT_POLL_INTERVAL_SECONDS", "15"))

CELERY_BEAT_SCHEDULE = {
    "heartbeat": {"task": "common.tasks.heartbeat", "schedule": 60.0},
    "ai-expire-approvals": {
        "task": "apps.ai_engine.tasks.expire_approvals",
        "schedule": 300.0,
    },
    "zalo-oa-refresh-tokens": {
        "task": "apps.channels.tasks.refresh_expiring_oa_tokens",
        "schedule": 600.0,
    },
    "aiva-deliver-outbox": {
        "task": "apps.integrations.aiva.tasks.deliver_due_outbox",
        "schedule": 60.0,
    },
    "zalo-bot-poll": {
        "task": "apps.channels.tasks.poll_bot_channels",
        "schedule": float(ZALO_BOT_POLL_INTERVAL_SECONDS),
    },
}

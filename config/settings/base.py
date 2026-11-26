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
    "default": dj_database_url.parse(
        env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"), conn_max_age=60
    )
}

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
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("JWT_REFRESH_DAYS", "7"))),
    "SIGNING_KEY": env("JWT_SECRET_KEY") or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Zalo Personal AI Assistant API",
    "DESCRIPTION": "Multi-tenant API (Phase 2: Zalo OA + Zalo Bot).",
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
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TIMEZONE = TIME_ZONE
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# --- Encryption of Zalo tokens / sessions ------------------------------------------
ENCRYPTION_KEY = env("ENCRYPTION_KEY")

# --- LLM (Anthropic via LLMProvider) ----------------------------------------------
LLM_PROVIDER = env("LLM_PROVIDER", "mock")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", "")
LLM_MODEL_MAIN = env("LLM_MODEL_MAIN", "claude-sonnet-5-5")
LLM_MODEL_FAST = env("LLM_MODEL_FAST", "claude-haiku-5-5")

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

# --- App behaviour -----------------------------------------------------------------
DEFAULT_AI_RESPONSE_DELAY_SECONDS = int(env("DEFAULT_AI_RESPONSE_DELAY_SECONDS", "3"))
APPROVAL_EXPIRY_HOURS = int(env("APPROVAL_EXPIRY_HOURS", "24"))
MAX_MEMORY_PER_CONTACT = int(env("MAX_MEMORY_PER_CONTACT", "100"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

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
    "zalo-oa-refresh-tokens": {
        "task": "apps.channels.tasks.refresh_expiring_oa_tokens",
        "schedule": 600.0,
    },
    "zalo-bot-poll": {
        "task": "apps.channels.tasks.poll_bot_channels",
        "schedule": float(ZALO_BOT_POLL_INTERVAL_SECONDS),
    },
}

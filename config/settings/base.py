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
    "DESCRIPTION": "Multi-tenant API (Phase 1 foundation).",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "ReplyPermissionEnum": "apps.contacts.models.ReplyPermission",
        "DataOriginEnum": "common.models.DataOrigin",
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

# real | mock. Phase 1 has no real Zalo provider, so mock is the default everywhere.
ZALO_CHANNEL_BACKEND = env("ZALO_CHANNEL_BACKEND", "mock")

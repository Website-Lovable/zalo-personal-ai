"""Production settings. Everything secret comes from the environment (see .env.production.example).

The app runs behind one reverse proxy (Caddy, deploy/Caddyfile) that terminates TLS and
appends to X-Forwarded-For; set TRUSTED_PROXY_COUNT accordingly (default 1 here).
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

for _required in ("SECRET_KEY", "ENCRYPTION_KEY", "DATABASE_URL", "JWT_SECRET_KEY"):
    if not env(_required):
        raise ImproperlyConfigured(f"{_required} must be set in production")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("change-me", "dev-insecure")):
    raise ImproperlyConfigured("SECRET_KEY is too short or a placeholder")
if env("JWT_SECRET_KEY") == SECRET_KEY:
    raise ImproperlyConfigured("JWT_SECRET_KEY must differ from SECRET_KEY")
if not env("ALLOWED_HOSTS"):
    raise ImproperlyConfigured("ALLOWED_HOSTS must list the public domain(s)")
try:
    from cryptography.fernet import Fernet

    Fernet(ENCRYPTION_KEY)
except (ValueError, TypeError) as exc:
    raise ImproperlyConfigured("ENCRYPTION_KEY must be a valid Fernet key") from exc

# --- HTTPS (TLS ends at Caddy) ---------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
# Internal probes (Docker healthcheck, Prometheus) talk plain HTTP on the private network.
SECURE_REDIRECT_EXEMPT = [r"^healthz/$", r"^readyz/$", r"^metrics/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)  # opt in once the domain is final
if not SECURE_HSTS_PRELOAD:
    SILENCED_SYSTEM_CHECKS = ["security.W021"]  # preload is a deliberate later decision
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS", ",".join(f"https://{host}" for host in ALLOWED_HOSTS)
)
# Internal callers on the private Docker network (healthcheck, Prometheus, the sidecar) use
# these Host headers. Caddy only forwards requests for DOMAIN, so the Internet cannot.
ALLOWED_HOSTS = [*ALLOWED_HOSTS, *env_list("INTERNAL_HOSTS", "web,127.0.0.1,localhost")]
TRUSTED_PROXY_COUNT = int(env("TRUSTED_PROXY_COUNT", "1"))
REST_FRAMEWORK["NUM_PROXIES"] = TRUSTED_PROXY_COUNT or None

# --- Shared state across processes ----------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("CACHE_REDIS_URL", REDIS_URL),
        "KEY_PREFIX": "zalo-ai",
    }
}
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

# --- API docs: only for signed-in users in production (finding S4) ---------------------
SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"] = ["rest_framework.permissions.IsAuthenticated"]
SPECTACULAR_SETTINGS["SERVE_AUTHENTICATION"] = [
    "rest_framework_simplejwt.authentication.JWTAuthentication",
    "rest_framework.authentication.SessionAuthentication",
]

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
for _required in ("SECRET_KEY", "ENCRYPTION_KEY", "DATABASE_URL"):
    if not env(_required):
        raise ImproperlyConfigured(f"{_required} must be set in production")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

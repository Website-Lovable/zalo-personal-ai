from .base import *  # noqa: F403

SECRET_KEY = "test-only-secret-key-not-used-anywhere-else"  # noqa: S105
SIMPLE_JWT["SIGNING_KEY"] = SECRET_KEY
# Fixed Fernet key used only in tests (not a real secret).
ENCRYPTION_KEY = "q2bEqgCFV9ZbRm1Gx2Qh3oN8bxQ0m7ZsQyKxTQkUo5k="  # noqa: S105
DEBUG = False
LLM_PROVIDER = "mock"
AIVA_INTEGRATION_MODE = "off"
ZALO_PERSONAL_UNOFFICIAL_ENABLED = False
CELERY_TASK_ALWAYS_EAGER = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
ZALO_CHANNEL_BACKEND = "mock"
PUBLIC_BASE_URL = "https://zalo-ai.test"
ZALO_OA_APP_ID = "test-app-id"
ZALO_OA_APP_SECRET = "test-app-secret"  # noqa: S105
ZALO_OA_WEBHOOK_SECRET = "test-oa-secret-key"  # noqa: S105
ZALO_OA_OAUTH_REDIRECT_URI = "https://zalo-ai.test/oauth/zalo-oa/callback/"
ZALO_OA_OAUTH_RESULT_URL = ""

from .base import *  # noqa: F403

DEBUG = env_bool("DEBUG", True)
if not SECRET_KEY:
    # Development only: a fixed, clearly-unsafe key so `docker compose up` works without .env.
    SECRET_KEY = "dev-insecure-key-do-not-use-in-production"  # noqa: S105
    SIMPLE_JWT["SIGNING_KEY"] = env("JWT_SECRET_KEY") or SECRET_KEY

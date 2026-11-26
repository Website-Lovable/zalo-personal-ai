import os

from channels.routing import ProtocolTypeRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_asgi_app = get_asgi_application()

# WebSocket routes are added in Phase 4 (real-time dashboard).
application = ProtocolTypeRouter({"http": django_asgi_app})

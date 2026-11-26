from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register("users", views.UserViewSet, basename="user")

auth_urlpatterns = [
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("refresh-token/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me/", views.MeView.as_view(), name="auth-me"),
]

urlpatterns = [path("tenant/", views.TenantView.as_view(), name="tenant"), *router.urls]

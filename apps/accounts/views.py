from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, viewsets
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from common.audit import log_action
from common.tenancy import get_request_tenant

from .models import User
from .rbac import IsTenantMember, RolePermission
from .serializers import (
    RegisterSerializer,
    TenantSerializer,
    TenantTokenObtainPairSerializer,
    UserSerializer,
)


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []
    # Brute force / signup spam protection (Phase 5 security review, finding S1).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"


class LoginView(TokenObtainPairView):
    serializer_class = TenantTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except (AuthenticationFailed, InvalidToken):
            username = str(request.data.get("username", ""))[:150]
            user = User.objects.filter(username=username).only("id", "tenant_id").first()
            log_action(
                "auth.login_failed",
                tenant=user.tenant if user else None,
                user=None,
                changes={"username": username},
                request=request,
            )
            raise


class RefreshTokenView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class TenantView(APIView):
    """GET for every member; PATCH (name, settings, external_ref) owner only."""

    permission_classes = [IsTenantMember, RolePermission]
    write_role = "owner"

    @extend_schema(responses=TenantSerializer)
    def get(self, request):
        return Response(TenantSerializer(get_request_tenant(request)).data)

    @extend_schema(request=TenantSerializer, responses=TenantSerializer)
    def patch(self, request):
        tenant = get_request_tenant(request)
        serializer = TenantSerializer(tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(
            "tenant.updated",
            tenant=tenant,
            obj=tenant,
            changes=dict(serializer.validated_data),
            request=request,
        )
        return Response(serializer.data)


class UserViewSet(viewsets.ModelViewSet):
    """Tenant user management (owner only for writes; admins can list)."""

    serializer_class = UserSerializer
    permission_classes = [IsTenantMember, RolePermission]
    read_role = "admin"
    write_role = "owner"
    queryset = User.objects.all().order_by("id")

    def get_queryset(self):
        return super().get_queryset().filter(tenant=get_request_tenant(self.request))

    def perform_create(self, serializer):
        user = serializer.save(tenant=get_request_tenant(self.request))
        log_action(
            "user.created",
            obj=user,
            tenant=user.tenant,
            changes={"role": user.role, "username": user.username},
            request=self.request,
        )

    def perform_update(self, serializer):
        user = serializer.save()
        log_action(
            "user.updated",
            obj=user,
            tenant=user.tenant,
            changes=dict(serializer.validated_data),
            request=self.request,
        )

    def perform_destroy(self, instance):
        log_action("user.deleted", obj=instance, tenant=instance.tenant, request=self.request)
        instance.delete()

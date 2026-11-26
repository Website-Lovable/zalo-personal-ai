from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.rbac import IsTenantMember, RolePermission
from common.audit import log_action
from common.tenancy import get_request_tenant

from .models import CommunicationStyle, OwnerProfile
from .serializers import CommunicationStyleSerializer, OwnerProfileSerializer


class _TenantSingletonView(APIView):
    """GET/PUT/PATCH a one-per-tenant settings object (created on first access)."""

    permission_classes = [IsTenantMember, RolePermission]
    model = None
    serializer_class = None
    audit_action = ""

    def get_object(self):
        obj, _ = self.model.objects.get_or_create(tenant=get_request_tenant(self.request))
        return obj

    def get(self, request):
        return Response(self.serializer_class(self.get_object()).data)

    def _write(self, request, partial):
        obj = self.get_object()
        serializer = self.serializer_class(obj, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(
            self.audit_action, obj=obj, changes=dict(serializer.validated_data), request=request
        )
        return Response(serializer.data)

    def put(self, request):
        return self._write(request, partial=False)

    def patch(self, request):
        return self._write(request, partial=True)


@extend_schema(request=OwnerProfileSerializer, responses=OwnerProfileSerializer)
class OwnerProfileView(_TenantSingletonView):
    model = OwnerProfile
    serializer_class = OwnerProfileSerializer
    audit_action = "ai_profile.updated"


@extend_schema(request=CommunicationStyleSerializer, responses=CommunicationStyleSerializer)
class CommunicationStyleView(_TenantSingletonView):
    model = CommunicationStyle
    serializer_class = CommunicationStyleSerializer
    audit_action = "ai_style.updated"

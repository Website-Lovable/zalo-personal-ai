from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.rbac import IsTenantMember, RolePermission
from common.tenancy import TenantScopedViewSet, get_request_tenant

from . import services
from .models import ApprovalQueue, PermissionRule
from .serializers import ApprovalDecisionSerializer, ApprovalSerializer, PermissionRuleSerializer


class PermissionRuleViewSet(TenantScopedViewSet):
    queryset = PermissionRule.objects.select_related("contact_group")
    serializer_class = PermissionRuleSerializer
    filterset_fields = ["action", "is_active", "data_origin"]
    audit_prefix = "permission_rule"


class ApprovalViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Approval queue. Approving an inbound-reply draft sends it through the channel.

    ``?mine=1`` lists the items assigned to the caller (``owner_staff_ref`` = their staff id).
    A staff member (any role) may decide items assigned to them; owner/admin decide all.
    """

    serializer_class = ApprovalSerializer
    permission_classes = [IsTenantMember, RolePermission]
    # Fine-grained check in services.can_decide (assigned staff may be viewers).
    action_roles = {"approve": "viewer", "reject": "viewer"}
    filterset_fields = ["status", "source", "kind", "owner_staff_ref", "conversation"]
    queryset = ApprovalQueue.objects.select_related("contact", "conversation", "message")

    def get_queryset(self):
        qs = super().get_queryset().filter(tenant=get_request_tenant(self.request))
        if self.request.query_params.get("mine") in ("1", "true"):
            staff_ref = self.request.user.staff_ref
            qs = qs.filter(owner_staff_ref=staff_ref) if staff_ref else qs.none()
        return qs

    def _decide(self, request, approve: bool):
        item = self.get_object()
        if not services.can_decide(request.user, item):
            raise PermissionDenied("Bạn không phụ trách khách này.")
        decision = ApprovalDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)
        data = decision.validated_data
        try:
            if approve:
                item = services.approve(
                    item,
                    request.user,
                    modified=data.get("modified_response", ""),
                    reason=data.get("reason", ""),
                    request=request,
                )
            else:
                item = services.reject(
                    item, request.user, reason=data.get("reason", ""), request=request
                )
        except services.ApprovalError as exc:
            if exc.status == 400:
                raise ValidationError({"status": str(exc)}) from exc
            item.refresh_from_db()
            return Response(
                {"detail": str(exc), "approval": self.get_serializer(item).data},
                status=exc.status,
            )
        return Response(self.get_serializer(item).data)

    @extend_schema(request=ApprovalDecisionSerializer, responses=ApprovalSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, approve=True)

    @extend_schema(request=ApprovalDecisionSerializer, responses=ApprovalSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, approve=False)

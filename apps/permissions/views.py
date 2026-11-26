from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.rbac import IsTenantMember, RolePermission
from common.audit import log_action
from common.tenancy import TenantScopedViewSet, get_request_tenant

from .models import ApprovalQueue, PermissionRule
from .serializers import ApprovalDecisionSerializer, ApprovalSerializer, PermissionRuleSerializer


class PermissionRuleViewSet(TenantScopedViewSet):
    queryset = PermissionRule.objects.select_related("contact_group")
    serializer_class = PermissionRuleSerializer
    filterset_fields = ["action", "is_active", "data_origin"]
    audit_prefix = "permission_rule"


class ApprovalViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Approval queue. Phase 1 records the decision only; sending is wired in Phase 3."""

    serializer_class = ApprovalSerializer
    permission_classes = [IsTenantMember, RolePermission]
    filterset_fields = ["status", "source", "owner_staff_ref"]
    queryset = ApprovalQueue.objects.all()

    def get_queryset(self):
        return super().get_queryset().filter(tenant=get_request_tenant(self.request))

    def _decide(self, request, approve: bool):
        item = self.get_object()
        if item.status != ApprovalQueue.Status.PENDING:
            raise ValidationError({"status": "Mục này đã được xử lý."})
        if item.expires_at <= timezone.now():
            item.status = ApprovalQueue.Status.EXPIRED
            item.save(update_fields=["status", "updated_at"])
            raise ValidationError({"status": "Mục này đã hết hạn."})
        decision = ApprovalDecisionSerializer(data=request.data)
        decision.is_valid(raise_exception=True)
        modified = decision.validated_data.get("modified_response", "")
        if approve:
            item.status = (
                ApprovalQueue.Status.MODIFIED if modified else ApprovalQueue.Status.APPROVED
            )
            item.modified_response = modified
        else:
            item.status = ApprovalQueue.Status.REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.save()
        log_action(
            f"approval.{item.status}",
            obj=item,
            request=request,
            changes={
                "owner_staff_ref": item.owner_staff_ref,
                "reason": decision.validated_data.get("reason", ""),
            },
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

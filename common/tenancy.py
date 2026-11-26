"""Tenant isolation building blocks for the API layer.

Every business query MUST go through these classes (CLAUDE.md rule 1):
- ``TenantScopedSerializer`` restricts every related-object field to the caller's tenant,
  so a client cannot attach tenant B's contact to tenant A's record by guessing an id.
- ``TenantScopedViewSet`` filters the queryset by ``request.user.tenant``, stamps the tenant
  on create, and writes an AuditLog entry for create/update/delete.
"""

from __future__ import annotations

from rest_framework import serializers, viewsets

from apps.accounts.rbac import IsTenantMember, RolePermission

from .audit import log_action


def get_request_tenant(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return getattr(user, "tenant", None)


def _has_tenant_field(model) -> bool:
    return any(f.name == "tenant" for f in model._meta.get_fields())


class TenantScopedSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tenant = self.context.get("tenant")
        for field in self.fields.values():
            relation = getattr(field, "child_relation", field)
            if isinstance(relation, serializers.PrimaryKeyRelatedField) and not relation.read_only:
                queryset = relation.queryset
                if queryset is not None and _has_tenant_field(queryset.model):
                    relation.queryset = (
                        queryset.filter(tenant=tenant) if tenant else queryset.none()
                    )


class TenantScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [IsTenantMember, RolePermission]
    audit_prefix: str = ""

    def get_tenant(self):
        return get_request_tenant(self.request)

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(tenant=self.get_tenant())

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant"] = self.get_tenant()
        return context

    def _audit(self, verb: str, instance, changes=None):
        prefix = self.audit_prefix or instance._meta.model_name
        log_action(
            f"{prefix}.{verb}",
            tenant=self.get_tenant(),
            obj=instance,
            changes=changes or {},
            request=self.request,
        )

    def perform_create(self, serializer):
        instance = serializer.save(tenant=self.get_tenant())
        self._audit("created", instance, dict(serializer.validated_data))

    def perform_update(self, serializer):
        instance = serializer.save()
        self._audit("updated", instance, dict(serializer.validated_data))

    def perform_destroy(self, instance):
        self._audit("deleted", instance)
        instance.delete()

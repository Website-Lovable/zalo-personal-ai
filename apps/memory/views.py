from common.tenancy import TenantScopedViewSet

from .models import LongTermMemory
from .serializers import LongTermMemoryWriteSerializer


class LongTermMemoryViewSet(TenantScopedViewSet):
    """Long-term memories per contact: the owner can correct or delete what the AI learned."""

    queryset = LongTermMemory.objects.select_related("contact")
    serializer_class = LongTermMemoryWriteSerializer
    filterset_fields = ["contact", "memory_type", "data_origin"]
    audit_prefix = "memory"

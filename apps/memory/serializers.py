from rest_framework import serializers

from common.tenancy import TenantScopedSerializer

from .models import LongTermMemory


class LongTermMemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = LongTermMemory
        fields = [
            "id",
            "contact",
            "memory_type",
            "content",
            "importance",
            "source_conversation",
            "last_accessed_at",
            "data_origin",
            "created_at",
        ]
        read_only_fields = fields


class LongTermMemoryWriteSerializer(TenantScopedSerializer):
    importance = serializers.IntegerField(min_value=1, max_value=10, required=False)

    class Meta:
        model = LongTermMemory
        fields = [
            "id",
            "contact",
            "memory_type",
            "content",
            "importance",
            "source_conversation",
            "last_accessed_at",
            "data_origin",
            "created_at",
        ]
        read_only_fields = ["id", "source_conversation", "last_accessed_at", "created_at"]

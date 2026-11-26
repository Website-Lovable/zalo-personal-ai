from rest_framework import serializers

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

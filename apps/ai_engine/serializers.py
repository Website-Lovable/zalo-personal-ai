from rest_framework import serializers

from .models import CommunicationStyle, OwnerProfile


class OwnerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = OwnerProfile
        exclude = ["tenant"]
        read_only_fields = ["id", "created_at", "updated_at"]


class CommunicationStyleSerializer(serializers.ModelSerializer):
    formality_level = serializers.IntegerField(min_value=1, max_value=10, required=False)

    class Meta:
        model = CommunicationStyle
        exclude = ["tenant"]
        read_only_fields = ["id", "created_at", "updated_at"]

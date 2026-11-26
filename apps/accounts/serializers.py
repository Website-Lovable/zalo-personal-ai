from django.contrib.auth import password_validation
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from common.audit import log_action

from .models import Tenant, User


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "slug",
            "subscription_tier",
            "is_active",
            "settings",
            "external_ref",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "slug",
            "is_active",
            "subscription_tier",
            "created_at",
            "updated_at",
        ]


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "staff_ref",
            "role",
            "is_active",
            "password",
            "tenant",
        ]
        read_only_fields = ["id", "tenant"]

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class RegisterSerializer(serializers.Serializer):
    """Self-service signup: creates a tenant and its owner user."""

    tenant_name = serializers.CharField(max_length=255)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Tên đăng nhập đã tồn tại.")
        return value

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value

    @staticmethod
    def _unique_slug(name: str) -> str:
        base = slugify(name) or "tenant"
        slug, n = base, 1
        while Tenant.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base}-{n}"
        return slug

    @transaction.atomic
    def create(self, validated_data):
        tenant = Tenant.objects.create(
            name=validated_data["tenant_name"],
            slug=self._unique_slug(validated_data["tenant_name"]),
        )
        user = User(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            tenant=tenant,
            role=User.Role.OWNER,
        )
        user.set_password(validated_data["password"])
        user.save()
        log_action(
            "tenant.registered",
            tenant=tenant,
            user=user,
            obj=tenant,
            request=self.context.get("request"),
        )
        return user

    def to_representation(self, user):
        refresh = RefreshToken.for_user(user)
        return {
            "user": UserSerializer(user).data,
            "tenant": TenantSerializer(user.tenant).data,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


class TenantTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT login; adds tenant + role claims (informational, server re-checks on each call)."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["tenant_id"] = user.tenant_id
        token["role"] = user.role
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        log_action(
            "auth.login",
            user=self.user,
            tenant=self.user.tenant,
            request=self.context.get("request"),
        )
        data["role"] = self.user.role
        data["tenant_id"] = self.user.tenant_id
        return data

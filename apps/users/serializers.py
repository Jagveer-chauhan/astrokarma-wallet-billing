from decimal import Decimal
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.users.models import User, UserRole


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "role",
            "per_minute_rate",
        ]
        read_only_fields = ["id"]


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "password",
            "role",
            "per_minute_rate",
        ]
        read_only_fields = ["id"]

    def validate_email(self, value):
        if value:
            normalized_email = value.strip().lower()
            if User.objects.filter(email__iexact=normalized_email).exists():
                raise serializers.ValidationError("A user with this email address already exists.")
            return normalized_email
        return value

    def validate(self, attrs):
        role = attrs.get("role", UserRole.USER)
        per_minute_rate = attrs.get("per_minute_rate")
        if role == UserRole.PROVIDER and per_minute_rate is not None and per_minute_rate <= Decimal("0.00"):
            raise serializers.ValidationError({"per_minute_rate": "Provider rate must be greater than zero."})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        validate_password(password)
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

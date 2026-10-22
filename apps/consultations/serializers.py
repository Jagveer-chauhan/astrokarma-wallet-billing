from rest_framework import serializers

from apps.consultations.models import ConsultationSession, SessionBillingRecord
from apps.users.serializers import UserSerializer


class SessionBillingRecordSerializer(serializers.ModelSerializer):
    transaction_reference = serializers.CharField(
        source="wallet_transaction.reference",
        read_only=True,
    )

    class Meta:
        model = SessionBillingRecord
        fields = [
            "id",
            "minute_number",
            "amount",
            "transaction_reference",
            "billed_at",
        ]
        read_only_fields = fields


class ConsultationSessionSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    provider = UserSerializer(read_only=True)
    billing_records = SessionBillingRecordSerializer(many=True, read_only=True)

    class Meta:
        model = ConsultationSession
        fields = [
            "id",
            "user",
            "provider",
            "status",
            "per_minute_rate",
            "started_at",
            "ended_at",
            "total_duration_seconds",
            "total_billed_minutes",
            "total_billed_amount",
            "ended_by",
            "end_reason",
            "billing_records",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CreateSessionSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField(required=False, allow_null=True)


class AssignProviderSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField(required=True)


class EndSessionSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=255,
        required=False,
        default="Completed by user/provider",
    )


class TriggerBillingSerializer(serializers.Serializer):
    target_minutes = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Optional target minutes to bill synchronously for test/simulation",
    )

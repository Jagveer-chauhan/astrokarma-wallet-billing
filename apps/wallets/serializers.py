from decimal import Decimal
from rest_framework import serializers

from apps.wallets.models import TransactionStatus, Wallet, WalletTransaction


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "amount",
            "transaction_type",
            "reference",
            "status",
            "description",
            "created_at",
        ]
        read_only_fields = fields


class WalletSerializer(serializers.ModelSerializer):
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            "id",
            "balance",
            "updated_at",
            "recent_transactions",
        ]
        read_only_fields = fields

    def get_recent_transactions(self, obj):
        recent = obj.transactions.all()[:10]
        return WalletTransactionSerializer(recent, many=True).data


class RechargeInitiateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    reference = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )


class RechargeCallbackSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100)
    status = serializers.ChoiceField(
        choices=[
            TransactionStatus.SUCCESS,
            TransactionStatus.FAILED,
        ]
    )

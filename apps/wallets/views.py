from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.wallets.models import Wallet, WalletTransaction
from apps.wallets.serializers import (
    RechargeCallbackSerializer,
    RechargeInitiateSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from apps.wallets.services import WalletService


class WalletDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        wallet = WalletService.get_or_create_wallet(request.user)
        serializer = WalletSerializer(wallet)
        return Response(serializer.data, status=status.HTTP_200_OK)


class RechargeInitiateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = RechargeInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data["amount"]
        reference = serializer.validated_data.get("reference") or None

        txn = WalletService.initiate_recharge(
            user=request.user,
            amount=amount,
            reference=reference,
        )

        return Response(
            {
                "message": "Recharge initiated successfully.",
                "transaction": WalletTransactionSerializer(txn).data,
            },
            status=status.HTTP_201_CREATED,
        )


class RechargeCallbackView(APIView):
    """
    Mock webhook receiver for payment provider callbacks.
    Idempotent: Multiple calls with the same reference and success status
    will credit the user's wallet only once.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RechargeCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reference = serializer.validated_data["reference"]
        callback_status = serializer.validated_data["status"]

        txn = WalletService.process_recharge_callback(
            reference=reference,
            callback_status=callback_status,
        )

        return Response(
            {
                "message": "Callback processed successfully.",
                "transaction": WalletTransactionSerializer(txn).data,
                "current_wallet_balance": str(txn.wallet.balance),
            },
            status=status.HTTP_200_OK,
        )


class WalletTransactionsListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        wallet = WalletService.get_or_create_wallet(self.request.user)
        return WalletTransaction.objects.filter(wallet=wallet)

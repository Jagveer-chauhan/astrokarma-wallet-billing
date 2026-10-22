from django.urls import path
from apps.wallets.views import (
    RechargeCallbackView,
    RechargeInitiateView,
    WalletDetailView,
    WalletTransactionsListView,
)

app_name = "wallets"

urlpatterns = [
    path("", WalletDetailView.as_view(), name="wallet_detail"),
    path("transactions/", WalletTransactionsListView.as_view(), name="wallet_transactions"),
    path("recharge/initiate/", RechargeInitiateView.as_view(), name="recharge_initiate"),
    path("recharge/callback/", RechargeCallbackView.as_view(), name="recharge_callback"),
]

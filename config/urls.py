from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.users.urls", namespace="users")),
    path("api/v1/wallet/", include("apps.wallets.urls", namespace="wallets")),
]

from django.urls import path
from apps.users.views import LoginView, RegisterView, UserProfileView

app_name = "users"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", UserProfileView.as_view(), name="me"),
]

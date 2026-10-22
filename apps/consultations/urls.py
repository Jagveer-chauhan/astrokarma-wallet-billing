from rest_framework.routers import DefaultRouter
from apps.consultations.views import ConsultationSessionViewSet

app_name = "consultations"

router = DefaultRouter()
router.register(r"", ConsultationSessionViewSet, basename="session")

urlpatterns = router.urls

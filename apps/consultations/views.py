from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.exceptions import BusinessRuleViolation
from apps.consultations.models import ConsultationSession
from apps.consultations.serializers import (
    AssignProviderSerializer,
    ConsultationSessionSerializer,
    CreateSessionSerializer,
    EndSessionSerializer,
    TriggerBillingSerializer,
)
from apps.consultations.services import BillingService, SessionService
from apps.users.models import User


class ConsultationSessionViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConsultationSessionSerializer
    http_method_names = ["get", "post"]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return ConsultationSession.objects.all().select_related("user", "provider")
        return ConsultationSession.objects.filter(
            Q(user=user) | Q(provider=user)
        ).select_related("user", "provider").prefetch_related("billing_records")

    def create(self, request, *args, **kwargs):
        serializer = CreateSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        provider_id = serializer.validated_data.get("provider_id")
        provider = None
        if provider_id:
            provider = get_object_or_404(User, id=provider_id)
            if not provider.is_provider:
                raise BusinessRuleViolation("Assigned user must have the PROVIDER role.")

        session = SessionService.create_session(
            user=request.user,
            provider=provider,
        )
        return Response(
            ConsultationSessionSerializer(session).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        serializer = AssignProviderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        provider = get_object_or_404(User, id=serializer.validated_data["provider_id"])
        session = SessionService.assign_provider(
            session_id=pk,
            user=request.user,
            provider=provider,
        )
        return Response(
            ConsultationSessionSerializer(session).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        session = SessionService.accept_session(
            session_id=pk,
            provider=request.user,
        )
        return Response(
            ConsultationSessionSerializer(session).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        session = SessionService.start_session(
            session_id=pk,
            user=request.user,
        )
        return Response(
            ConsultationSessionSerializer(session).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="end")
    def end(self, request, pk=None):
        serializer = EndSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reason = serializer.validated_data.get("reason", "Completed by user/provider")
        session = SessionService.end_session(
            session_id=pk,
            requested_by_user=request.user,
            reason=reason,
        )
        return Response(
            ConsultationSessionSerializer(session).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="bill")
    def bill(self, request, pk=None):
        serializer = TriggerBillingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_minutes = serializer.validated_data.get("target_minutes")
        result = BillingService.process_session_billing(
            session_id=pk,
            target_minutes=target_minutes,
        )
        return Response(result, status=status.HTTP_200_OK)

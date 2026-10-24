from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.common.exceptions import BusinessRuleViolation


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if isinstance(exc, BusinessRuleViolation):
        return Response(
            {
                "error": exc.message,
                "code": exc.code,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, (ObjectDoesNotExist, Http404)):
        return Response(
            {
                "error": "The requested resource was not found.",
                "code": "not_found",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    return response

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

    return response

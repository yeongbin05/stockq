# config/exception_handlers.py
from rest_framework.views import exception_handler
from rest_framework import serializers

def custom_exception_handler(exc, context):
    # 기본 DRF handler 호출 (response 생성)
    response = exception_handler(exc, context)

    if response is not None:
        # ValidationError일 경우
        if isinstance(exc, serializers.ValidationError):
            # exc.detail 이 dict일 수도 있고 str일 수도 있음
            detail = response.data.get("detail", response.data)

            response.data = {
                "code": "VALIDATION_ERROR",
                "message": detail,      # 프론트에서 에러 메시지 쉽게 꺼낼 수 있게
                "errors": response.data # 원본 에러 구조도 같이 보존
            }

        # PermissionDenied, NotAuthenticated 등도 통일 가능
        elif response.status_code == 403:
            response.data = {
                "code": "FORBIDDEN",
                "message": "접근 권한이 없습니다.",
                "errors": response.data,
            }
        elif response.status_code == 401:
            response.data = {
                "code": "UNAUTHORIZED",
                "message": "인증이 필요합니다.",
                "errors": response.data,
            }

    return response

import time
from django.urls import resolve, Resolver404
from .metrics import API_REQUESTS_TOTAL, API_REQUEST_LATENCY_SECONDS


class ApiMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        response = None
        status_code = 500

        try:
            response = self.get_response(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            path = request.path

            should_record = path.startswith("/api/") and path != "/metrics"

            if should_record:
                try:
                    match = resolve(path)
                    route = match.route or path
                except Resolver404:
                    route = path

                method = request.method
                status = str(status_code)

                API_REQUESTS_TOTAL.labels(
                    route=route,
                    method=method,
                    status=status,
                ).inc()

                API_REQUEST_LATENCY_SECONDS.labels(
                    route=route,
                    method=method,
                ).observe(duration)
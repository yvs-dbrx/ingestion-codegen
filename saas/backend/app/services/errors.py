"""One base class for "a rule was violated" errors, so a single handler in main.py turns any of
them into the right HTTP response (`{"detail": "..."}` plus any extra fields, e.g. `problems`)."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str, **extra):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class Unprocessable(ServiceError):
    status_code = 422


class BadGateway(ServiceError):
    """Something outside this service (Databricks) failed."""

    status_code = 502

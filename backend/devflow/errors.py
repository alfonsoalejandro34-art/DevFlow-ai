"""Application exceptions. The API layer maps each to an HTTP status and error code."""

from __future__ import annotations


class DevFlowError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationFailed(DevFlowError):
    status_code = 422
    code = "validation_failed"


class SecurityViolation(DevFlowError):
    status_code = 403
    code = "security_violation"


class NotFound(DevFlowError):
    status_code = 404
    code = "not_found"


class Conflict(DevFlowError):
    status_code = 409
    code = "conflict"


class ServiceUnavailable(DevFlowError):
    status_code = 503
    code = "service_unavailable"


class CommandFailed(DevFlowError):
    status_code = 500
    code = "command_failed"

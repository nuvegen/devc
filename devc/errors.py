"""Fehlerklassen, Codes und Exit-Codes (SPEC 8.1) sowie Prüfbefunde (SPEC 9)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """Befund einer Prüfung L*n* oder W*n*."""

    check: str
    path: str
    message: str
    severity: str = "error"
    line: int | None = None

    def to_dict(self) -> dict:
        data = {
            "check": self.check,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }
        if self.line is not None:
            data["line"] = self.line
        return data

    def as_line(self) -> str:
        if self.path:
            return f"{self.check} {self.path}: {self.message}"
        return f"{self.check}: {self.message}"


def has_errors(findings) -> bool:
    return any(f.severity == "error" for f in findings)


class DevcError(Exception):
    """Basis aller Fehler, die devc nach außen meldet."""

    code = "E_INTERNAL"
    exit_code = 1
    http_status = 500

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "details": self.details}


class InternalError(DevcError):
    pass


class JobFailedError(DevcError):
    code = "E_JOB_FAILED"

    def __init__(self, message: str, job: str | None = None, details: dict | None = None):
        details = dict(details or {})
        if job is not None:
            details["job"] = job
        super().__init__(message, details)


class UsageError(DevcError):
    code = "E_USAGE"
    exit_code = 2
    http_status = 400


class EngineError(DevcError):
    code = "E_ENGINE"
    exit_code = 3
    http_status = 502


class NotFoundError(DevcError):
    code = "E_NOT_FOUND"
    exit_code = 4
    http_status = 404


class ConflictError(DevcError):
    code = "E_CONFLICT"
    exit_code = 5
    http_status = 409


class ValidationError(DevcError):
    code = "E_VALIDATION"
    exit_code = 5
    http_status = 422

    def __init__(self, findings, message: str | None = None):
        self.findings = list(findings)
        errors = [f for f in self.findings if f.severity == "error"] or self.findings
        details = {}
        if errors:
            first = errors[0]
            details["check"] = first.check
            if first.path:
                details["path"] = first.path
            if first.line is not None:
                details["line"] = first.line
            if message is None:
                message = first.message
        if len(self.findings) > 1:
            details["findings"] = [f.to_dict() for f in self.findings]
        super().__init__(message or "Prüfung fehlgeschlagen", details)

    @classmethod
    def single(cls, check: str, path: str, message: str, line: int | None = None):
        return cls([Finding(check, path, message, line=line)])


class MissingSecretError(DevcError):
    code = "E_MISSING_SECRET"
    exit_code = 5
    http_status = 422

    def __init__(self, slots, message: str | None = None):
        slots = list(slots)
        if message is None:
            message = "Secret fehlt: " + ", ".join(slots)
        super().__init__(message, {"slots": slots})


class SecurityError(DevcError):
    code = "E_SECURITY"
    exit_code = 6
    http_status = 403


class AuthError(DevcError):
    """Nur HTTP; die CLI kennt keinen eigenen Exit-Code dafür."""

    code = "E_AUTH"
    http_status = 401


ERROR_CLASSES = (
    InternalError,
    JobFailedError,
    UsageError,
    EngineError,
    NotFoundError,
    ConflictError,
    ValidationError,
    MissingSecretError,
    SecurityError,
    AuthError,
)

# Code -> (Exit, HTTP-Status), wie Tabelle SPEC 8.1
CODES = {cls.code: (cls.exit_code, cls.http_status) for cls in ERROR_CLASSES}

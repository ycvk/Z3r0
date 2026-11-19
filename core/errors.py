"""Domain errors raised by services and entry points.

`middleware/common/response.py` maps each subclass to an HTTP status code.
"""


class DomainError(Exception):
    """Base class for expected domain failures."""


class ValidationError(DomainError):
    """Input, state, or precondition does not allow the requested action."""


class AuthenticationError(DomainError):
    """Credentials are missing or invalid."""


class ForbiddenError(DomainError):
    """The authenticated actor lacks the role required for the action."""


class NotFoundError(DomainError):
    """The target resource does not exist or is not visible to the actor."""


class ConflictError(DomainError):
    """The action conflicts with an existing record or concurrent work."""


class UpstreamError(DomainError):
    """An external daemon or control proxy failed to complete the operation."""

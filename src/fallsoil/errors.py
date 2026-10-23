"""Exception hierarchy. Every error carries a friendly `user_message` suitable for display."""
from __future__ import annotations


class FallsoilError(Exception):
    """Base class. `str(error)` is the friendly message."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class ConfigError(FallsoilError):
    """Missing or unusable configuration (e.g. data directory)."""


class UnknownBasinError(FallsoilError, LookupError):
    """Basin id is not in the pilot registry."""


class DataValidationError(FallsoilError):
    """Structural problem with a data frame (types, missing columns, empty)."""


class ManifestError(FallsoilError):
    """The provenance manifest is corrupt; the message names the offending line."""


class FetchError(FallsoilError):
    """A remote fetch failed."""


class HttpError(FetchError):
    """HTTP failure. `status_code` is None for timeouts and connection errors."""

    def __init__(self, user_message: str, *, status_code: int | None = None, attempts: int = 1):
        super().__init__(user_message)
        self.status_code = status_code
        self.attempts = attempts


class MalformedPayloadError(FetchError):
    """The remote service returned data that does not have the expected shape or units."""


class EmptyResultError(FetchError):
    """The remote service returned no data for the request."""


class ZeroOverlapError(FetchError, ValueError):
    """A basin polygon has no overlap with the data grid."""

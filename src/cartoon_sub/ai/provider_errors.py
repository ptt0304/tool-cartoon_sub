"""Provider-neutral failures shared by AI gateway adapters."""
from enum import Enum

from cartoon_sub.ai.gemini_client import GeminiError


class ProviderErrorCategory(str, Enum):
    AUTH_INVALID = "AUTH_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    KEY_BUDGET_EXCEEDED = "KEY_BUDGET_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    MODEL_ERROR = "MODEL_ERROR"
    RESPONSE_ERROR = "RESPONSE_ERROR"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class AIProviderError(GeminiError):
    """Neutral runtime error with GeminiError compatibility for legacy callers."""

    def __init__(self, message, category=ProviderErrorCategory.UNKNOWN, *,
                 status_code=None, retryable=False, retry_after_seconds=None,
                 provider_code=None, provider_message=None):
        if not isinstance(category, ProviderErrorCategory):
            category = ProviderErrorCategory(category)
        super().__init__(
            message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            status_code=status_code,
            category=category.value,
        )
        self.error_category = category
        self.provider_code = provider_code
        self.provider_message = provider_message

"""Provider-neutral failures shared by OpenRouter adapters."""
from enum import Enum

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

class AIProviderError(RuntimeError):
    def __init__(self, message, category=ProviderErrorCategory.UNKNOWN, *, status_code=None, retryable=False, retry_after_seconds=None, provider_code=None, provider_message=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code
        self.category = category.value if isinstance(category, ProviderErrorCategory) else str(category)
        self.error_category = category if isinstance(category, ProviderErrorCategory) else ProviderErrorCategory.UNKNOWN
        self.provider_code = provider_code
        self.provider_message = provider_message

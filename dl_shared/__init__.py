try:
    from dl_shared.rate_limit import RateLimitMiddleware
except ImportError:
    RateLimitMiddleware = None  # type: ignore[assignment]

try:
    from dl_shared.secrets import SecretsManager
except ImportError:
    SecretsManager = None  # type: ignore[assignment]

__all__ = ["RateLimitMiddleware", "SecretsManager"]

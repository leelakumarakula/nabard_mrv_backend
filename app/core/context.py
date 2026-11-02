from contextvars import ContextVar

# Correlation/request ID for the current request; set by middleware, read by audit and error handlers.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

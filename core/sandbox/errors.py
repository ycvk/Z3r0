class SandboxContainerCommandTimeoutError(TimeoutError):
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Sandbox container command timed out after {_format_timeout_seconds(timeout_seconds)} seconds")


def _format_timeout_seconds(timeout_seconds: float) -> str:
    value = float(timeout_seconds)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")

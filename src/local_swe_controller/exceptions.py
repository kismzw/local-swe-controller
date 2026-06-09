"""Custom exceptions for local_swe_controller."""


class LocalSweError(Exception):
    """Base exception for application errors."""


class ConfigError(LocalSweError):
    """Raised when configuration cannot be loaded or validated."""


class PolicyCompileError(LocalSweError):
    """Raised when policy compilation cannot proceed."""


class SandboxError(LocalSweError):
    """Raised when sandbox preparation or cleanup fails."""


class CommandSafetyError(SandboxError):
    """Raised when a command violates sandbox safety policy."""


class ValidationError(LocalSweError):
    """Raised when validation cannot proceed or complete safely."""


class RepairError(LocalSweError):
    """Raised when repair cannot proceed or complete safely."""


class OrchestrationError(LocalSweError):
    """Raised when optional orchestration cannot proceed safely."""


class PRWorkflowError(LocalSweError):
    """Raised when PR summary generation or PR creation cannot proceed safely."""


class BenchmarkError(LocalSweError):
    """Raised when benchmark loading or execution cannot proceed safely."""

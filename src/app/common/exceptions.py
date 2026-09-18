"""Application-wide exception types."""


class AppError(Exception):
    """Base class for all application errors."""


class ConfigError(AppError):
    """Raised when configuration is missing or invalid."""


class StorageError(AppError):
    """Raised on storage layer failures (parquet / sqlite)."""


class CollectorError(AppError):
    """Raised on CTP collector failures."""


class ReplayError(AppError):
    """Raised on replay failures."""


class AnalysisError(AppError):
    """Raised on analysis pipeline failures."""


class DataIntegrityError(AppError):
    """Raised when raw data integrity verification fails."""

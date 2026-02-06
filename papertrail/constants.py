"""Shared constants used across the papertrail package."""

import functools
import inspect
from typing import Any, Callable, TypeVar

# Field names used by MappingsManager and RejectedValuesManager
FIELDS = ("document_types", "issuing_parties")

# Type variable for return types
T = TypeVar('T')


def validate_field(default_return: T, field_param: str = "field") -> Callable:
    """Decorator to validate that a field argument is in the class's FIELDS tuple.

    Args:
        default_return: Value to return if field validation fails
        field_param: Name of the parameter containing the field value (default: "field")

    Returns:
        Decorated function that checks field validity before execution
    """
    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs) -> Any:
            # Get field value from args or kwargs
            field_value = None
            if field_param in kwargs:
                field_value = kwargs[field_param]
            else:
                # Find position of field_param (excluding 'self')
                try:
                    param_idx = params.index(field_param) - 1  # -1 for 'self'
                    if param_idx < len(args):
                        field_value = args[param_idx]
                except (ValueError, IndexError):
                    pass

            if field_value is None or field_value not in self.FIELDS:
                return default_return
            return func(self, *args, **kwargs)
        return wrapper
    return decorator

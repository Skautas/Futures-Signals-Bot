"""
Async Safety Engine Module
Safe async function calls with error handling
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class AsyncResult:
    success: bool
    value: Any = None
    error: Optional[str] = None


async def safe_call(func: Callable, *args, **kwargs) -> AsyncResult:
    """Safely call async function with error handling"""
    try:
        result = await func(*args, **kwargs)
        return AsyncResult(success=True, value=result)
    except Exception as e:
        return AsyncResult(success=False, error=str(e))


def guard_boolean(value: Any, default: bool = False) -> bool:
    """Guard boolean value"""
    if isinstance(value, bool):
        return value
    return default


def guard_numeric(value: Any, default: float = 0.0) -> float:
    """Guard numeric value"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_len(seq: Any) -> int:
    """Safely get length of sequence"""
    try:
        return len(seq)
    except (TypeError, AttributeError):
        return 0


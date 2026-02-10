from typing import Callable, Any


class ModeRouter:
    def __init__(self):
        self._handlers = {}

    def register(self, mode: str, handler: Callable[..., Any]):
        self._handlers[mode.upper()] = handler

    def route(self, mode: str, *args, **kwargs):
        handler = self._handlers.get(mode.upper())
        if not handler:
            raise ValueError(f"Unknown mode: {mode}")
        return handler(*args, **kwargs)

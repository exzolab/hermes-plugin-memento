# pyrefly: ignore[missing-import]
from .provider import MementoProvider


def register(ctx) -> None:
    ctx.register_memory_provider(MementoProvider())

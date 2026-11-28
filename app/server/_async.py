"""Bridge synchronous SDK calls into async code without losing request context."""

import asyncio
import contextvars
import functools
from typing import Callable, ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def run_sync(fn: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Run a blocking callable in the default executor with the caller's contextvars."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, contextvars.copy_context().run, functools.partial(fn, *args, **kwargs))

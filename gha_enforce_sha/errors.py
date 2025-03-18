from __future__ import annotations

import traceback


class UserError(Exception):
    pass


def log_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, UserError):
        return 1, f"Error: {exc}"
    elif isinstance(exc, KeyboardInterrupt):  # pragma: no cover
        return 130, "Interrupted (^C)"
    else:  # pragma: no cover
        return (
            2,
            f"Fatal: unexpected error: '{exc}'\n"
            + "Please report this bug with the following traceback:\n"
            + "".join(traceback.format_exception(None, exc, exc.__traceback__)),
        )

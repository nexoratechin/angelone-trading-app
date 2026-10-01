"""Emergency kill switch.

Two ways to stop trading immediately:

  * touch/create the kill-switch file (works even if the process is wedged), or
  * call :meth:`trip` programmatically (used when the daily loss limit trips).

Once tripped, the risk manager blocks all new entries. Exits are still allowed
so an open position is never trapped.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..logging import get_logger

log = get_logger("risk.kill_switch")


class KillSwitch:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._tripped = False
        self._reason = ""

    def is_active(self) -> bool:
        if self._tripped or self.path.exists():
            return True
        return False

    @property
    def reason(self) -> str:
        if self.path.exists():
            try:
                return self.path.read_text(encoding="utf-8").strip() or "kill switch file present"
            except OSError:
                return "kill switch file present"
        return self._reason

    def trip(self, reason: str) -> None:
        self._tripped = True
        self._reason = reason
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(reason, encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            log.error("Could not write kill switch file: %s", exc)
        log.error("KILL SWITCH TRIPPED: %s", reason)

    def reset(self) -> None:
        self._tripped = False
        self._reason = ""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            log.warning("Could not remove kill switch file: %s", exc)

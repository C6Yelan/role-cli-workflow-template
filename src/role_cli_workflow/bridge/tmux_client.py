"""Minimal fixed-socket tmux transport for the Project role bridge."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass

from .config import TMUX_SOCKET
from .models import RoleConfig

TMUX_BIN = "tmux"
SUBMISSION_DELAY_SECONDS = 0.250


class TmuxError(RuntimeError):
    """Sanitized fixed-target tmux failure."""


@dataclass(frozen=True)
class PaneInfo:
    pane_id: str
    current_command: str
    pane_pid: int
    dead: bool
    history_size: int
    cursor_y: int


class TmuxClient:
    def _run(
        self,
        arguments: list[str],
        *,
        input_text: str | None = None,
        timeout: float = 10.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [TMUX_BIN, "-S", str(TMUX_SOCKET), *arguments]
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TmuxError("tmux operation failed") from exc
        if check and completed.returncode != 0:
            raise TmuxError("tmux operation failed")
        return completed

    def socket_exists(self) -> bool:
        return TMUX_SOCKET.is_socket()

    def session_exists(self, role: RoleConfig) -> bool:
        return self._run(["has-session", "-t", role.session], check=False).returncode == 0

    def pane_info(self, role: RoleConfig) -> PaneInfo:
        target = f"{role.session}:{role.window}"
        fmt = "#{pane_id}\t#{pane_current_command}\t#{pane_pid}\t#{pane_dead}\t#{history_size}\t#{cursor_y}"
        output = self._run(["list-panes", "-t", target, "-F", fmt]).stdout
        lines = [line for line in output.splitlines() if line]
        if len(lines) != 1:
            raise TmuxError("fixed role pane is unavailable")
        fields = lines[0].split("\t")
        if len(fields) != 6:
            raise TmuxError("fixed role pane is unavailable")
        try:
            return PaneInfo(
                pane_id=fields[0],
                current_command=fields[1],
                pane_pid=int(fields[2]),
                dead=fields[3] != "0",
                history_size=int(fields[4]),
                cursor_y=int(fields[5]),
            )
        except ValueError as exc:
            raise TmuxError("fixed role pane is unavailable") from exc

    def fixed_pane(self, role: RoleConfig) -> PaneInfo:
        if not self.socket_exists() or not self.session_exists(role):
            raise TmuxError("fixed role pane is unavailable")
        pane = self.pane_info(role)
        if pane.dead or not re.fullmatch(r"%[0-9]+", pane.pane_id):
            raise TmuxError("fixed role pane is unavailable")
        return pane

    def probe(self, role: RoleConfig) -> tuple[str, PaneInfo | None]:
        """Return ALIVE, DOWN, or UNKNOWN without inferring from pane text."""
        if not self.socket_exists():
            return "DOWN", None
        try:
            session = self._run(["has-session", "-t", role.session], check=False)
            if session.returncode == 1:
                return "DOWN", None
            if session.returncode != 0:
                return "UNKNOWN", None
            pane = self.pane_info(role)
        except TmuxError:
            return "UNKNOWN", None
        if pane.dead or not re.fullmatch(r"%[0-9]+", pane.pane_id):
            return "UNKNOWN", pane
        if pane.pane_pid > 0:
            return "ALIVE", pane
        return "UNKNOWN", pane

    def paste(self, pane_id: str, text: str, buffer_name: str) -> None:
        if not re.fullmatch(r"%[0-9]+", pane_id):
            raise TmuxError("fixed pane identity is invalid")
        if not re.fullmatch(r"crw-[0-9a-f]{16}", buffer_name):
            raise TmuxError("tmux buffer identity is invalid")
        self._run(["load-buffer", "-b", buffer_name, "-"], input_text=text)
        try:
            self._run(["paste-buffer", "-d", "-b", buffer_name, "-t", pane_id])
            time.sleep(SUBMISSION_DELAY_SECONDS)
            self._run(["send-keys", "-t", pane_id, "Enter"])
        finally:
            self._run(["delete-buffer", "-b", buffer_name], check=False)

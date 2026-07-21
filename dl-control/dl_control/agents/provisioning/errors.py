"""Provisioning error type — carries the failing step for audit + status."""

from __future__ import annotations


class ProvisioningError(RuntimeError):
    """A provisioning/restart step failed. `step` names the failing step;
    the service layer writes it to the audit log and sets status='error'."""

    def __init__(self, step: str, message: str, *, agent_id: str | None = None):
        super().__init__(f"{step}: {message}")
        self.step = step
        self.agent_id = agent_id

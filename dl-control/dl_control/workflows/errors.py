"""Workflow-runner exception taxonomy (spec §5.4, §6.2).

The ledger exceptions are the side-effect contract: KnownCleanError is the
ONLY signal that a failed external call provably had no effect (and is
therefore retryable); anything else ambiguous becomes UnresolvedEffectError
and parks the run for manual confirmation — never an auto-retry (Codex R4 P1).
"""

from __future__ import annotations


class WorkflowError(RuntimeError):
    """Base for all workflow-runner errors."""


class UnknownWorkflowError(WorkflowError):
    """start_run named a workflow that is not registered."""


class WorkflowDisabledError(WorkflowError):
    """start_run named a workflow whose admin toggle is off."""


class DuplicateActiveRunError(WorkflowError):
    """A live run already exists for (workflow_id, correlation_key) — §5.2."""


class FlowLoadError(WorkflowError):
    """code_ref rejected by the allowlist or not loadable as a Flow — §11."""


class KnownCleanError(WorkflowError):
    """Raise from perform() when the provider PROVABLY did not act (connection
    refused before send, synchronous validation rejection, 4xx before the
    provider acted). Safe to retry under the step's retry policy — §5.4."""


class LedgerConflictError(WorkflowError):
    """Same idempotency key, different request_hash — the flow changed its
    mind. Reject, do not overwrite, surface — §5.4."""


class UnresolvedEffectError(WorkflowError):
    """The effect MAY have fired (pre-existing 'started' row, or perform
    raised ambiguously). The run must park in waiting_manual — §5.4."""


class ApprovalNotPendingError(WorkflowError):
    """decide_approval hit an approval or run not in the expected state."""


class LeaseLostError(WorkflowError):
    """A guarded run mutation found lease_owner changed — abandon, never
    double-advance (spec §13 concurrency)."""


class UnknownScheduleError(WorkflowError):
    """A schedule mutation named a workflow_schedule row that does not exist."""


class ManualTransitionError(WorkflowError):
    """A manual control / waiting_manual resolution found the run (or ledger
    row) not in the expected state — the conditional-UPDATE arbiter lost
    (D-P13C-5). Surfaced as 409 in the admin UI."""

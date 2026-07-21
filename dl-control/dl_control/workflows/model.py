"""Code-first flow definitions (spec §6.1).

A Flow is an ordered sequence of Steps. A Step is exactly one of:
- a handler step — async callable, optional Retry policy, optional pre-step
  Approval gate;
- a timer step — a pure durable wait (no handler, no retry, no approval);
- a call_agent step — dispatches work to a downstream agent, optional Retry
  policy, optional pre-step Approval gate.

Branching: a handler returns StepResult(goto=...) naming the next step key;
returning None or a plain value falls through to the next step in list order;
goto=DONE (or falling off the end) terminates the run as succeeded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from dl_control.db import Database

DONE = "__done__"  # reserved goto target: terminate the run as succeeded


@dataclass(frozen=True)
class Retry:
    """Exponential backoff for known-clean failures (spec §6.2 step 4)."""

    max_attempts: int = 3
    base_seconds: float = 30.0
    cap_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_seconds <= 0:
            raise ValueError("base_seconds must be > 0")
        if self.cap_seconds < self.base_seconds:
            raise ValueError("cap_seconds must be >= base_seconds")

    def delay_after(self, attempt: int) -> float:
        """Backoff after the attempt-th failed attempt (1-based)."""
        return min(self.cap_seconds, self.base_seconds * 2 ** (attempt - 1))


@dataclass(frozen=True)
class Timer:
    """A durable wait (spec §6.1 — e.g. Timer(days=3))."""

    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0

    def __post_init__(self) -> None:
        if self.total_seconds <= 0:
            raise ValueError("Timer must be positive")

    @property
    def total_seconds(self) -> int:
        return ((self.days * 24 + self.hours) * 60 + self.minutes) * 60 + self.seconds


@dataclass(frozen=True)
class Approval:
    """A pre-step human gate (spec §5.5)."""

    prompt: str


@dataclass(frozen=True)
class AgentTask:
    """What a call_agent step dispatches: the target agent + the task message."""

    agent_id: UUID
    message: str


PrepareTask = Callable[[dict[str, Any], dict[str, Any]], AgentTask]


@dataclass(frozen=True)
class CallAgent:
    """A workflow→agent step (spec §7.1). prepare(input, outputs) builds the
    AgentTask and MUST be deterministic — it is re-evaluated on every
    (re-)post and its request_hash must match the minted correlation; a
    mismatch is the §5.4 'flow changed its mind' conflict (D-P13D-5)."""

    prepare: PrepareTask
    timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")


@dataclass(frozen=True)
class StepResult:
    """What a handler may return: an output to persist and/or a branch."""

    output: Any = None
    goto: str | None = None


Handler = Callable[["StepContext"], Awaitable["StepResult | Any"]]


@dataclass(frozen=True)
class Step:
    key: str
    handler: Handler | None = None
    timer: Timer | None = None
    call_agent: CallAgent | None = None
    retry: Retry | None = None
    approval: Approval | None = None

    def __post_init__(self) -> None:
        kinds = sum(x is not None for x in (self.handler, self.timer, self.call_agent))
        if kinds != 1:
            raise ValueError(f"step {self.key!r}: exactly one of handler/timer/call_agent")
        if self.timer is not None and (self.retry or self.approval):
            raise ValueError(f"step {self.key!r}: timer steps take no retry/approval")


@dataclass(frozen=True)
class Flow:
    """An ordered, validated flow definition. steps is normalized to a tuple."""

    id: str
    version: str
    steps: tuple[Step, ...]
    by_key: dict[str, Step] = field(init=False, repr=False)

    def __init__(self, id: str, version: str, steps: Sequence[Step]) -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "steps", tuple(steps))
        if not self.steps:
            raise ValueError(f"flow {id!r}: needs at least one step")
        keys = [s.key for s in self.steps]
        if len(set(keys)) != len(keys):
            raise ValueError(f"flow {id!r}: duplicate step keys")
        if DONE in keys:
            raise ValueError(f"flow {id!r}: step key {DONE!r} is reserved")
        object.__setattr__(self, "by_key", {s.key: s for s in self.steps})

    @property
    def first_key(self) -> str:
        return self.steps[0].key

    def next_key(self, key: str) -> str | None:
        """The key after `key` in list order, or None at the end."""
        keys = [s.key for s in self.steps]
        i = keys.index(key)
        return keys[i + 1] if i + 1 < len(keys) else None


@dataclass
class StepContext:
    """What a handler sees: run identity, input, prior step outputs, and the
    ledger helper. Handlers MUST route every external mutation through
    ctx.ledgered (W8) — a direct external write is a Reviewer-gate failure."""

    run_id: UUID
    workflow_id: str
    step_key: str
    attempt: int
    input: dict[str, Any]
    outputs: dict[str, Any]
    db: Database = field(repr=False)

    async def ledgered(
        self,
        *,
        target: str,
        request: dict[str, Any],
        perform,
        call_site: str | None = None,
    ) -> Any:
        """Execute one external mutation under the §5.4 ledger contract.

        The idempotency key is (run_id, step_key[, call_site]) — stable across
        retries by construction. A step performing MORE than one external
        mutation must give each call a distinct call_site (§5.3)."""
        from dl_control.workflows.ledger import ledgered_call

        key = f"{self.run_id}:{self.step_key}"
        if call_site:
            key = f"{key}:{call_site}"
        return await ledgered_call(
            self.db,
            key=key,
            run_id=self.run_id,
            step_key=self.step_key,
            attempt=self.attempt,
            target=target,
            request=request,
            perform=perform,
        )

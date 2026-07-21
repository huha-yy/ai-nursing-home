"""HR onboarding emails — the W9 pilot (spec §12).

validate (no retry, fail fast) → welcome_email (ledgered send, retry 5) →
await_day3 (durable timer) → manager_intro (approval gate + ledgered send).
Both sends go through ctx.ledgered (W8); SMTP config is resolved from env at
call time (spec §11 — credentials never persist in run rows).
"""

from __future__ import annotations

from typing import Any

from dl_control.workflows.email_send import SmtpConfig, send_email
from dl_control.workflows.model import (
    Approval,
    Flow,
    Retry,
    Step,
    StepContext,
    Timer,
)

REQUIRED_KEYS = ("employee_id", "employee_name", "employee_email", "manager_email")


async def _perform_send(request: dict[str, Any]) -> dict[str, Any]:
    """Resolved at call time so tests can monkeypatch this module attribute."""
    from dl_control.settings import load_settings

    return await send_email(SmtpConfig.from_settings(load_settings()), request)


async def validate(ctx: StepContext) -> dict[str, Any]:
    missing = [k for k in REQUIRED_KEYS if not ctx.input.get(k)]
    if missing:
        # No Retry on this step → default max_attempts=1 → fails fast.
        raise ValueError(f"missing input keys: {missing}")
    return {"validated": True}


async def welcome_email(ctx: StepContext) -> dict[str, Any]:
    name = ctx.input["employee_name"]
    request = {
        "to": ctx.input["employee_email"],
        "subject": f"Welcome aboard, {name}!",
        "body": (
            f"Hi {name},\n\n"
            "Welcome to the team! Your accounts are being prepared; "
            "your manager will introduce themselves in a few days.\n\n"
            "— HR onboarding (automated)"
        ),
    }
    return await ctx.ledgered(target="email", request=request, perform=_perform_send)


async def manager_intro(ctx: StepContext) -> dict[str, Any]:
    name = ctx.input["employee_name"]
    request = {
        "to": ctx.input["manager_email"],
        "subject": f"Manager intro: {name} has joined",
        "body": (
            f"Hello,\n\n{name} ({ctx.input['employee_email']}) joined three "
            "days ago. Please schedule a 1:1 introduction this week.\n\n"
            "— HR onboarding (automated)"
        ),
    }
    return await ctx.ledgered(target="email", request=request, perform=_perform_send)


flow = Flow(
    "hr.onboarding_email",
    version="1.0.0",
    steps=[
        Step("validate", handler=validate),
        Step("welcome_email", handler=welcome_email, retry=Retry(max_attempts=5)),
        Step("await_day3", timer=Timer(days=3)),
        Step(
            "manager_intro",
            handler=manager_intro,
            retry=Retry(max_attempts=5),
            approval=Approval("Send the manager-introduction email?"),
        ),
    ],
)

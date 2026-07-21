"""code_ref → Flow with the spec §11 allowlist.

workflow_version.code_ref lives in the database; a DB-level compromise must
not let the runner import an arbitrary module:attr. Only refs under the
shipped flows package pass. Tests inject allowed_package to load fixtures;
the production runner always uses the default.
"""

from __future__ import annotations

import importlib

from dl_control.workflows.errors import FlowLoadError
from dl_control.workflows.model import Flow

FLOWS_PACKAGE = "dl_control.workflows.flows"


def load_flow(code_ref: str, *, allowed_package: str = FLOWS_PACKAGE) -> Flow:
    """Resolve 'module:attr' to a Flow instance, allowlist-enforced."""
    module_path, sep, attr = code_ref.partition(":")
    if not sep or not attr or not module_path:
        raise FlowLoadError(f"bad code_ref {code_ref!r} (want 'module:attr')")
    if not module_path.startswith(allowed_package + "."):
        raise FlowLoadError(
            f"code_ref {code_ref!r}: module outside allowed package {allowed_package!r}"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise FlowLoadError(f"code_ref {code_ref!r}: import failed: {exc}") from exc
    flow = getattr(module, attr, None)
    if not isinstance(flow, Flow):
        raise FlowLoadError(f"code_ref {code_ref!r}: {attr!r} is not a Flow")
    return flow

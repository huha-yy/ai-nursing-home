"""P8 precreated agents — declarative-seed layer.

Reconciler at startup materializes built-in seed agents
(e.g. agent-manager) into the agent registry via the existing
registry.insert_agent + provision_agent path.

Admins can suppress unwanted seeds; updates to seed definitions
surface as drift without overwriting admin-tuned state.
"""

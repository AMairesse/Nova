"""
Wrappers around the original nova.forms so they silently accept the
`user` keyword injected by OwnerFormKwargsMixin and by Tool form-set helpers.
"""

from nova.forms import (
    LLMProviderForm as _LLMProviderForm,
    AgentForm as _AgentForm,
    ToolForm as _ToolForm,
    ToolCredentialForm as _ToolCredentialForm,
)


# ───────────────────────────────────────────────────────────────
# Provider
# ───────────────────────────────────────────────────────────────
class LLMProviderForm(_LLMProviderForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


# ───────────────────────────────────────────────────────────────
# Agent
# ───────────────────────────────────────────────────────────────
class AgentForm(_AgentForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


# ───────────────────────────────────────────────────────────────
# Tool
# ───────────────────────────────────────────────────────────────
class ToolForm(_ToolForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


# ───────────────────────────────────────────────────────────────
# ToolCredential  (inline formset)
# ───────────────────────────────────────────────────────────────
class ToolCredentialForm(_ToolCredentialForm):
    def __init__(self, *args, user=None, tool=None, **kwargs):
        # store whatever you may need for custom clean() later
        self.user = user
        self.tool = tool
        super().__init__(*args, **kwargs)

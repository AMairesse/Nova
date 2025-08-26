from nova.forms import (
    LLMProviderForm as _LLMProviderForm,
    AgentForm as _AgentForm,
    ToolForm as _ToolForm,
    ToolCredentialForm as _ToolCredentialForm,
)

# -------- déjà présents ---------------------------------------------------
class LLMProviderForm(_LLMProviderForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class AgentForm(_AgentForm):
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)


# -------- nouveaux wrappers ----------------------------------------------
class ToolForm(_ToolForm):
    def __init__(self, *args, user=None, **kwargs):
        # On conserve l’utilisateur pour filtrer les FK éventuels
        self.user = user
        super().__init__(*args, **kwargs)


class ToolCredentialForm(_ToolCredentialForm):
    """Le mixin injecte user ; on le transmet à la sous-form."""
    def __init__(self, *args, user=None, tool=None, **kwargs):
        super().__init__(*args, user=user, tool=tool, **kwargs)

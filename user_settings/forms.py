from nova.forms import (
    LLMProviderForm as _LLMProviderForm,
    AgentForm as _AgentForm,
)


class LLMProviderForm(_LLMProviderForm):
    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class AgentForm(_AgentForm):
    """
    Capture l’argument `user` injecté par OwnerFormKwargsMixin.
    Le formulaire d’origine le gère déjà, on se contente de forwarder.
    """
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)

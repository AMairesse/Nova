from nova.forms import LLMProviderForm as _LLMProviderForm

#   ┌────────────────────────────────────────────────────────────┐
#   │  Wrapper : capture le paramètre `user` (optionnel)         │
#   └────────────────────────────────────────────────────────────┘
class LLMProviderForm(_LLMProviderForm):
    """
    Proxy autour du formulaire original pour qu'il accepte
    user=None passé par OwnerFormKwargsMixin.
    """
    def __init__(self, *args, user=None, **kwargs):
        # On stocke au besoin l'utilisateur pour de futures
        # validations (ex. clé API unique par utilisateur).
        self.user = user
        super().__init__(*args, **kwargs)

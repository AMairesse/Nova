# nova/llm/exceptions.py
class AskUserPause(Exception):
    """Raised by the ask_user tool to stop the agent execution and wait for user input."""
    def __init__(self, interaction_id: int, message: str | None = None):
        self.interaction_id = interaction_id
        super().__init__(message or "Awaiting user input")

from nova.models.Message import Actor


def actor_enum(request):
    """
    Make the Actor choices available in every Django template:
        {{ Actor.USER }} ⇒ "USR"
        {{ Actor.AGENT }} ⇒ "AGT"
    """
    return {"Actor": Actor}

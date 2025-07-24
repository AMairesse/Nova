# nova/signals.py

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile, UserParameters

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile_and_params(sender, instance, created, **kwargs):
    """
    Automatically create UserProfile and UserParameters when a new User is created.
    """
    if created:
        UserProfile.objects.create(user=instance)
        UserParameters.objects.create(user=instance)

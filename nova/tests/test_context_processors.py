# nova/tests/test_context_processors.py
from django.test import SimpleTestCase, RequestFactory, override_settings
from django.template import engines

from nova.context_processors import actor_enum
from nova.models.Message import Actor


class ContextProcessorsTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_actor_enum_injects_actor_class(self):
        request = self.factory.get("/")
        context = actor_enum(request)

        self.assertIsInstance(context, dict)
        self.assertIn("Actor", context)
        self.assertIs(context["Actor"], Actor)

    @override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {
                    "context_processors": [
                        "nova.context_processors.actor_enum",
                    ]
                },
            }
        ]
    )
    def test_actor_is_available_in_templates(self):
        request = self.factory.get("/")
        django_engine = engines["django"]

        expected_user_value = str(Actor.USER)
        expected_agent_value = str(Actor.AGENT)

        template = django_engine.from_string("""
            {{ Actor.USER }}|{{ Actor.AGENT }}
        """)
        rendered = template.render({}, request=request).strip()
        self.assertEqual(rendered,
                         f"{expected_user_value}|{expected_agent_value}")

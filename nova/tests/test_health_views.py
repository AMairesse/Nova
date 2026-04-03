from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings

from nova.views.health import healthz


class HealthzViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(DEBUG=False)
    @patch("nova.views.health.redis.Redis")
    def test_healthz_returns_200_when_dependencies_are_ready(self, mocked_redis):
        mocked_redis.return_value.ping.return_value = True

        response = healthz(self.factory.get("/healthz/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "OK")
        mocked_redis.return_value.ping.assert_called_once_with()

    @override_settings(DEBUG=False)
    @patch("nova.views.health._check_database", side_effect=Exception("db down"))
    def test_healthz_returns_503_when_database_unavailable(self, mocked_database):
        response = healthz(self.factory.get("/healthz/"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content.decode(), "Database unavailable")
        mocked_database.assert_called_once_with()

    @override_settings(DEBUG=False)
    @patch("nova.views.health._check_redis", side_effect=Exception("redis down"))
    def test_healthz_returns_503_when_redis_unavailable(self, mocked_redis):
        response = healthz(self.factory.get("/healthz/"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content.decode(), "Redis unavailable")
        mocked_redis.assert_called_once_with()

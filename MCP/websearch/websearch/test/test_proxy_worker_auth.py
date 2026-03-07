import unittest
from unittest.mock import patch

from websearch.utils.proxy import apply_worker_auth


class ProxyWorkerAuthTests(unittest.TestCase):
    def test_apply_worker_auth_adds_bearer_for_worker_request(self) -> None:
        with patch(
            "websearch.utils.proxy.get_config",
            return_value=type("Cfg", (), {"cf_worker_url": "https://worker.example", "cf_worker_token": "secret"})(),
        ):
            headers = apply_worker_auth({"Accept": "text/html"}, "https://worker.example?url=https%3A%2F%2Fexample.com")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Accept"], "text/html")

    def test_apply_worker_auth_keeps_existing_auth_header(self) -> None:
        with patch(
            "websearch.utils.proxy.get_config",
            return_value=type("Cfg", (), {"cf_worker_url": "https://worker.example", "cf_worker_token": "secret"})(),
        ):
            headers = apply_worker_auth(
                {"Authorization": "Bearer existing"},
                "https://worker.example?url=https%3A%2F%2Fexample.com",
            )
        self.assertEqual(headers["Authorization"], "Bearer existing")

    def test_apply_worker_auth_does_not_touch_direct_request(self) -> None:
        with patch(
            "websearch.utils.proxy.get_config",
            return_value=type("Cfg", (), {"cf_worker_url": "https://worker.example", "cf_worker_token": "secret"})(),
        ):
            headers = apply_worker_auth({"Accept": "text/html"}, "https://example.com")
        self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main()

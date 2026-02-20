import asyncio
import unittest
from unittest.mock import patch

from websearch.tools.fetch_search_core import _curl_get_with_retries, _search_duckduckgo_core
from websearch.utils.config import _reset_runtime_for_tests, init_runtime


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RetryLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _reset_runtime_for_tests()
        init_runtime(argv=[])

    def test_curl_retries_on_chunked_stream_read_failure(self) -> None:
        with (
            patch(
                "websearch.tools.fetch_search_core.curl_requests.get",
                side_effect=[
                    RuntimeError(
                        "Failed to perform, curl: (23) Failed reading the chunked-encoded stream."
                    ),
                    _FakeResponse("<html>ok</html>", 200),
                ],
            ) as mock_get,
            patch("websearch.tools.fetch_search_core.time.sleep", return_value=None),
        ):
            response = _curl_get_with_retries("https://example.com", timeout_s=1, retries=2)
            self.assertEqual(response.text, "<html>ok</html>")
            self.assertEqual(mock_get.call_count, 2)

    def test_curl_does_not_retry_on_non_retryable_error(self) -> None:
        with patch(
            "websearch.tools.fetch_search_core.curl_requests.get",
            side_effect=RuntimeError("Failed to perform, curl: (6) Could not resolve host: example.com"),
        ) as mock_get:
            with self.assertRaises(RuntimeError):
                _curl_get_with_retries("https://example.com", timeout_s=1, retries=3)
            self.assertEqual(mock_get.call_count, 1)

    def test_ddg_search_uses_shared_retry_helper(self) -> None:
        html = """
        <html><body>
          <div class="results">
            <div class="result">
              <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3Dc">
                Example
              </a>
              <a class="result__snippet">Snippet</a>
            </div>
          </div>
        </body></html>
        """
        with patch(
            "websearch.tools.fetch_search_core._curl_get_with_retries",
            return_value=_FakeResponse(html, 200),
        ) as mock_retry:
            results = asyncio.run(_search_duckduckgo_core("test query", max_results=1))

        self.assertEqual(mock_retry.call_count, 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.com/a?b=c")


if __name__ == "__main__":
    unittest.main()

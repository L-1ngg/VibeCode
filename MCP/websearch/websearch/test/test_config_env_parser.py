import io
import os
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from websearch.utils.env_parser import load_env_file


class ConfigEnvParserTests(unittest.TestCase):
    def _load_from_text(self, text: str) -> None:
        tmp_root = Path("websearch/test/_tmp_env_parser")
        tmp_root.mkdir(parents=True, exist_ok=True)
        env_path = tmp_root / f"{uuid4().hex}.env"
        try:
            env_path.write_text(text, encoding="utf-8")
            load_env_file(env_path)
        finally:
            try:
                env_path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_inline_comment_is_ignored_for_unquoted_values(self) -> None:
        key = "TEST_INLINE_COMMENT"
        os.environ.pop(key, None)
        self._load_from_text(f"{key}=value # this is comment\n")
        self.assertEqual(os.environ.get(key), "value")
        os.environ.pop(key, None)

    def test_hash_without_space_is_kept(self) -> None:
        key = "TEST_HASH_KEEP"
        os.environ.pop(key, None)
        self._load_from_text(f"{key}=abc#def\n")
        self.assertEqual(os.environ.get(key), "abc#def")
        os.environ.pop(key, None)

    def test_nested_quotes_are_preserved_inside_outer_quotes(self) -> None:
        key1 = "TEST_NESTED_SINGLE"
        key2 = "TEST_NESTED_DOUBLE"
        os.environ.pop(key1, None)
        os.environ.pop(key2, None)
        self._load_from_text(f'{key1}="\'hello\'"\n{key2}=\'"hello"\'\n')
        self.assertEqual(os.environ.get(key1), "'hello'")
        self.assertEqual(os.environ.get(key2), '"hello"')
        os.environ.pop(key1, None)
        os.environ.pop(key2, None)

    def test_multiline_quoted_value_is_supported(self) -> None:
        key = "TEST_MULTILINE"
        os.environ.pop(key, None)
        self._load_from_text(f'{key}="line1\nline2\nline3"\n')
        self.assertEqual(os.environ.get(key), "line1\nline2\nline3")
        os.environ.pop(key, None)

    def test_read_oserror_is_reported_in_stderr(self) -> None:
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            fake_stderr = io.StringIO()
            with patch("websearch.utils.env_parser.sys.stderr", fake_stderr):
                load_env_file(Path("missing/.env"))
            self.assertIn("failed to read env file", fake_stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

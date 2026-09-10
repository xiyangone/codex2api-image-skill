import tempfile
import unittest
from pathlib import Path

from codex2api_image.config import ConfigError, load_config, parse_dotenv


class ConfigTests(unittest.TestCase):
    def test_dedicated_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CODEX2API_API_KEY=test-key\nCODEX2API_BASE_URL=http://127.0.0.1:8080/v1/\n")
            config = load_config(env_file=path, environ={})
            self.assertEqual(config.base_url, "http://127.0.0.1:8080/v1")
            self.assertEqual(config.api_key, "test-key")
            self.assertNotIn("test-key", repr(config))

    def test_dedicated_process_variables_override_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CODEX2API_API_KEY=file-key\n")
            config = load_config(env_file=path, environ={"CODEX2API_API_KEY": "process-key", "CODEX2API_BASE_URL": "https://example.test/v1"})
            self.assertEqual(config.api_key, "process-key")
            self.assertEqual(config.base_url, "https://example.test/v1")

    def test_generic_openai_variables_cannot_mix_endpoint_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CODEX2API_API_KEY=local-key\nOPENAI_BASE_URL=https://file.example/v1\n")
            config = load_config(env_file=path, environ={"OPENAI_BASE_URL": "https://other.example/v1", "OPENAI_API_KEY": "other-key"})
            self.assertEqual(config.base_url, "http://127.0.0.1:8080/v1")
            self.assertEqual(config.api_key, "local-key")

    def test_missing_dedicated_key_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY=not-a-local-key\n")
            with self.assertRaises(ConfigError):
                load_config(env_file=path, environ={"OPENAI_API_KEY": "ignored"})

    def test_missing_explicit_file_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                load_config(env_file=Path(tmp) / "missing.env", environ={})

    def test_dotenv_bom_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CODEX2API_API_KEY='quoted'\n", encoding="utf-8-sig")
            self.assertEqual(parse_dotenv(path)["CODEX2API_API_KEY"], "quoted")

    def test_invalid_base_urls_do_not_echo_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CODEX2API_API_KEY=test\n")
            for url in ("file:///tmp/key", "https://user:secret@example.test/v1", "https://example.test/v1?key=secret", "http://example.test:bad/v1"):
                with self.subTest(url=url), self.assertRaises(ConfigError) as caught:
                    load_config(env_file=path, environ={}, base_url=url)
                self.assertNotIn("secret", str(caught.exception))

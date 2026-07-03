import os
import tempfile
import unittest
from pathlib import Path


class ConfigTests(unittest.TestCase):
    def test_env_file_key_is_required_when_file_exists(self) -> None:
        from codex2api_image.config import ConfigError, load_config

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "CODEX2API_BASE_URL=http://127.0.0.1:8080/v1\nCODEX2API_API_KEY=\n",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(env_file=env_file, environ={})

    def test_env_file_key_and_base_url_are_loaded(self) -> None:
        from codex2api_image.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "CODEX2API_BASE_URL=http://127.0.0.1:8080/v1\nCODEX2API_API_KEY=sk-test\n",
                encoding="utf-8",
            )
            cfg = load_config(env_file=env_file, environ={})
            self.assertEqual(cfg.base_url, "http://127.0.0.1:8080/v1")
            self.assertEqual(cfg.api_key, "sk-test")

    def test_process_environment_overrides_env_file(self) -> None:
        from codex2api_image.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "CODEX2API_BASE_URL=http://127.0.0.1:8080/v1\nCODEX2API_API_KEY=sk-file\n",
                encoding="utf-8",
            )
            cfg = load_config(
                env_file=env_file,
                environ={
                    "CODEX2API_BASE_URL": "http://127.0.0.1:9999/v1",
                    "CODEX2API_API_KEY": "sk-process",
                },
            )
            self.assertEqual(cfg.base_url, "http://127.0.0.1:9999/v1")
            self.assertEqual(cfg.api_key, "sk-process")

    def test_default_env_file_points_to_skill_root(self) -> None:
        from codex2api_image.paths import default_env_file

        self.assertEqual(default_env_file().name, ".env")
        self.assertEqual(default_env_file().parent.name, "codex2api-image")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONPATH", "src")
    unittest.main()

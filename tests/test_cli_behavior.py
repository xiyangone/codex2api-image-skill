import argparse
import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any


def base_args(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "model": "gpt-image-2",
        "size": "auto",
        "quality": "auto",
        "output_format": "png",
        "response_format": "b64_json",
        "background": "auto",
        "moderation": "low",
        "output_compression": None,
        "n": 1,
        "style": "",
        "upscale": "",
        "timeout": 1,
        "poll_interval": 0.01,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CLIBehaviorTests(unittest.TestCase):
    def test_parser_rejects_input_fidelity(self) -> None:
        from codex2api_image.cli import build_parser

        parser = build_parser()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "edit",
                    "--prompt",
                    "test",
                    "--image",
                    "data:image/png;base64,AAAA",
                    "--out",
                    "out.png",
                    "--input-fidelity",
                    "high",
                ]
            )

    def test_batch_row_rejects_input_fidelity(self) -> None:
        from codex2api_image.cli import build_options
        from codex2api_image.errors import CommandError

        with self.assertRaisesRegex(CommandError, "input_fidelity"):
            build_options(base_args(), {"input_fidelity": "high"})

    def test_transparent_background_is_rejected(self) -> None:
        from codex2api_image.cli import build_options
        from codex2api_image.errors import CommandError

        with self.assertRaisesRegex(CommandError, "transparent"):
            build_options(base_args(background="transparent"))

    def test_prompt_for_request_is_unchanged_by_default(self) -> None:
        from codex2api_image.cli import prompt_for_request

        self.assertEqual(prompt_for_request("remove background", base_args(), None), "remove background")

    def test_prompt_for_request_wraps_when_cli_flag_enabled(self) -> None:
        from codex2api_image.cli import prompt_for_request

        prompt = prompt_for_request("remove background", base_args(clean_background=True), None)

        self.assertIn("plain clean light background", prompt)
        self.assertIn("User request: remove background", prompt)

    def test_prompt_for_request_wraps_when_batch_row_enabled(self) -> None:
        from codex2api_image.cli import prompt_for_request

        prompt = prompt_for_request("remove background", base_args(), {"clean_background": True})

        self.assertIn("plain clean light background", prompt)
        self.assertIn("User request: remove background", prompt)

    def test_prompt_for_request_batch_row_can_disable_global_flag(self) -> None:
        from codex2api_image.cli import prompt_for_request

        prompt = prompt_for_request("remove background", base_args(clean_background=True), {"clean_background": False})

        self.assertEqual(prompt, "remove background")

    def test_prompt_for_request_rejects_invalid_batch_clean_background(self) -> None:
        from codex2api_image.cli import prompt_for_request
        from codex2api_image.errors import CommandError

        with self.assertRaisesRegex(CommandError, "clean_background"):
            prompt_for_request("remove background", base_args(), {"clean_background": "maybe"})

    def test_batch_output_paths_must_be_unique(self) -> None:
        from codex2api_image.cli import ensure_unique_batch_outputs
        from codex2api_image.errors import CommandError

        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"prompt": "a", "out": "same.png"}, {"prompt": "b", "out": "same.png"}]
            with self.assertRaisesRegex(CommandError, "same.png"):
                ensure_unique_batch_outputs(rows, Path(tmp))

    def test_run_batch_rows_preserves_order_with_concurrency(self) -> None:
        from codex2api_image.cli import run_batch_rows

        rows = [{"prompt": "slow"}, {"prompt": "fast"}]

        def runner(index: int, row: dict[str, Any]) -> dict[str, Any]:
            if row["prompt"] == "slow":
                time.sleep(0.05)
            return {"index": index, "prompt": row["prompt"]}

        results = run_batch_rows(rows, concurrency=2, runner=runner)
        self.assertEqual([item["prompt"] for item in results], ["slow", "fast"])

    def test_run_batch_rows_reports_job_index_on_failure(self) -> None:
        from codex2api_image.cli import run_batch_rows
        from codex2api_image.errors import CommandError

        def runner(index: int, row: dict[str, Any]) -> dict[str, Any]:
            if index == 2:
                raise CommandError("boom")
            return {"index": index}

        with self.assertRaisesRegex(CommandError, "job 2: boom"):
            run_batch_rows([{"prompt": "a"}, {"prompt": "b"}], concurrency=2, runner=runner)


if __name__ == "__main__":
    unittest.main()

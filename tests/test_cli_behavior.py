import argparse
import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


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
        "auto_retry": False,
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

    def test_run_batch_rows_records_job_index_on_failure(self) -> None:
        from codex2api_image.cli import run_batch_rows
        from codex2api_image.errors import CommandError

        def runner(index: int, row: dict[str, Any]) -> dict[str, Any]:
            if index == 2:
                raise CommandError("boom")
            return {"index": index}

        results = run_batch_rows([{"prompt": "a"}, {"prompt": "b"}], concurrency=2, runner=runner)

        self.assertEqual(results[0], {"index": 1})
        self.assertEqual(results[1]["index"], 2)
        self.assertFalse(results[1]["ok"])
        self.assertIn("boom", results[1]["error"])

    def test_batch_row_defaults_to_direct_edit_when_images_exist(self) -> None:
        from codex2api_image.cli import run_batch_row

        args = base_args(clean_background=False)
        row = {
            "prompt": "change outfit",
            "_resolved_images": ["data:image/png;base64,AAAA"],
            "out": "out.png",
        }

        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.save_sync_with_auto_retry") as save_sync, patch(
            "codex2api_image.cli.run_job_with_auto_retry"
        ) as run_job:
            save_sync.return_value = (Path(tmp) / "out.png", [{"reason": "original", "ok": True}])
            result = run_batch_row(object(), args, 1, row, Path(tmp))  # type: ignore[arg-type]

        self.assertEqual(result["mode"], "edit")
        save_sync.assert_called_once()
        run_job.assert_not_called()

    def test_auto_retry_can_be_enabled_per_batch_row(self) -> None:
        from codex2api_image.cli import auto_retry_enabled

        self.assertTrue(auto_retry_enabled(base_args(), {"auto_retry": True}))
        self.assertFalse(auto_retry_enabled(base_args(auto_retry=True), {"auto_retry": False}))

    def test_hard_sensitive_refusal_is_detected(self) -> None:
        from codex2api_image.cli import is_image_output_rejection, is_sensitive_refusal

        error = "HTTP 422 Unprocessable Entity: image_output_rejected: Sorry, I can’t help create a nude version of this scene."

        self.assertTrue(is_image_output_rejection(error))
        self.assertTrue(is_sensitive_refusal(error))

    def test_sexualized_soft_rejection_can_try_prompt_reframes(self) -> None:
        from codex2api_image.cli import is_image_output_rejection, is_sensitive_refusal

        error = "HTTP 422 Unprocessable Entity: image_output_rejected: request may sexualize non-sensitive non-explicit visible legs/footwear."

        self.assertTrue(is_image_output_rejection(error))
        self.assertFalse(is_sensitive_refusal(error))

    def test_policy_rejection_stops_before_technical_fallbacks(self) -> None:
        from codex2api_image.cli import save_sync_with_auto_retry
        from codex2api_image.errors import CommandError
        from codex2api_image.payloads import ImageOptions

        args = base_args(auto_retry=True)
        calls: list[str] = []

        def reject(*_args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs["options"].output_format)
            raise CommandError("HTTP 422 Unprocessable Entity: image_output_rejected")

        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.request_once", reject):
            with self.assertRaisesRegex(CommandError, "all image attempts failed"):
                save_sync_with_auto_retry(
                    client=object(),  # type: ignore[arg-type]
                    args=args,
                    row=None,
                    prompt="upscale this specific image",
                    options=ImageOptions(model="gpt-image-2", output_format="png"),
                    mode="edit",
                    images=("data:image/png;base64,AAAA",),
                    out=Path(tmp) / "out.png",
                )

        self.assertEqual(calls, ["png", "png", "png", "png", "png"])

    def test_policy_prompt_reasons_match_retry_plan(self) -> None:
        from codex2api_image.cli import is_policy_prompt_retry
        from codex2api_image.payloads import ImageOptions, auto_retry_attempts

        reasons = [reason for reason, _, _ in auto_retry_attempts("enhance", ImageOptions(output_format="png"))]
        policy_reasons = [reason for reason in reasons if is_policy_prompt_retry(reason)]

        self.assertEqual(
            policy_reasons,
            [
                "original",
                "sanitized_user_prompt",
                "t1_conservative_restoration_prompt",
                "t2_conservative_quality_cleanup_prompt",
                "t3_modest_outfit_reframe_prompt",
            ],
        )

    def test_hard_sensitive_refusal_stops_immediately(self) -> None:
        from codex2api_image.cli import save_sync_with_auto_retry
        from codex2api_image.errors import CommandError
        from codex2api_image.payloads import ImageOptions

        args = base_args(auto_retry=True)
        calls = 0

        def reject(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            raise CommandError("HTTP 422 Unprocessable Entity: image_output_rejected nude version")

        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.request_once", reject):
            with self.assertRaisesRegex(CommandError, "sensitive image refusal"):
                save_sync_with_auto_retry(
                    client=object(),  # type: ignore[arg-type]
                    args=args,
                    row=None,
                    prompt="enhance",
                    options=ImageOptions(model="gpt-image-2", output_format="png"),
                    mode="edit",
                    images=("data:image/png;base64,AAAA",),
                    out=Path(tmp) / "out.png",
                )

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()

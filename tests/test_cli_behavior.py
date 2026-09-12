import base64
import contextlib
import io
import json
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from codex2api_image import cli
from codex2api_image.config import RuntimeConfig
from codex2api_image.errors import CommandError
from codex2api_image.http_client import Codex2APIClient


def invoke(args):
    stdout, stderr = io.StringIO(), io.StringIO()
    # unittest enables library deprecations that normal CLI entry points hide.
    # Keep those diagnostic warnings separate from the CLI's JSON streams.
    with warnings.catch_warnings(record=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def encoded_image():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def sample_job(status="succeeded", requested=1, completed=1):
    return {"id": 9, "status": status, "requested_outputs": requested, "completed_outputs": completed,
            "params_json": json.dumps({"n": requested, "model": "gpt-image-2.5-flare", "size": "16x16"}),
            "assets": [{"cache_b64_json": encoded_image()} for _ in range(completed)]}


class CLIBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.client = Codex2APIClient(RuntimeConfig("http://127.0.0.1:8080/v1", "test-only-key"))

    def test_generate_dry_run_needs_no_credentials_or_client(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", side_effect=AssertionError("must not load credentials")):
            code, stdout, stderr = invoke(["generate", "--prompt", "  cat\n", "--out", str(Path(tmp) / "out.png"), "--dry-run"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["payload"]["prompt"], "  cat\n")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_job_dry_run_preserves_exact_canvas_and_count(self):
        code, stdout, _ = invoke(["job", "submit", "--prompt", "wallpaper", "--model", "gpt-image-2.5-flare", "--quality", "max",
                                 "--size", "1920x1080", "--n", "4", "--strict-size", "--upscale-fit", "pad", "--dry-run"])
        self.assertEqual(code, 0)
        payload = json.loads(stdout)["payload"]
        self.assertEqual((payload["size"], payload["n"], payload["quality"]), ("1920x1080", 4, "max"))

    def test_reference_dry_run_redacts_image_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = "data:image/png;base64," + encoded_image()
            code, stdout, _ = invoke(["edit", "--prompt", "cat", "--image", image, "--out", str(Path(tmp) / "out.png"), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertNotIn("base64", stdout)
            self.assertEqual(json.loads(stdout)["payload"]["images"], [{"image_url": "<image 1>"}])

    def test_documented_generate_batch_works_without_images(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=[{"mode": "generate", "prompt": "A small orange cat", "out": "cat.png"}]), patch("codex2api_image.cli.client_from_args", side_effect=AssertionError("offline")):
            code, stdout, stderr = invoke(["batch", "--input", "mock.json", "--out-dir", tmp, "--dry-run"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["jobs"][0]["route"], "/images/generations")

    def test_batch_preflight_prevents_partial_submission(self):
        rows = [{"prompt": "valid"}, {"prompt": ""}]
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=rows), patch("codex2api_image.cli.client_from_args") as client:
            code, _, stderr = invoke(["batch", "--input", "mock.json", "--out-dir", tmp])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stderr)["error"]["index"], 2)
            client.assert_not_called()

    def test_batch_output_collisions_and_traversal_prevent_submission(self):
        for rows in ([{"prompt": "a", "out": "a.png"}, {"prompt": "b", "out": "A.png"}], [{"prompt": "a", "out": "../escape.png"}]):
            with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=rows), patch("codex2api_image.cli.client_from_args") as client:
                code, _, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp])
                self.assertEqual(code, 1)
                client.assert_not_called()

    def test_batch_count_generated_paths_are_preflighted(self):
        rows = [{"mode": "job", "prompt": "a", "n": 2, "out": "a.png"}, {"prompt": "b", "out": "a-001.png"}]
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=rows), patch("codex2api_image.cli.client_from_args") as client:
            code, _, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp])
            self.assertEqual(code, 1)
            client.assert_not_called()

    def test_batch_failures_return_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=[{"prompt": "a"}]), patch("codex2api_image.cli.client_from_args", return_value=self.client), patch("codex2api_image.cli.run_prepared", side_effect=CommandError("failed")):
            code, stdout, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stdout)["failed"], 1)

    def test_batch_results_keep_input_order(self):
        def finish(client, args, prepared):
            if prepared.payload["prompt"] == "slow":
                time.sleep(0.02)
            return {"ok": True, "name": prepared.payload["prompt"]}
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=[{"prompt": "slow"}, {"prompt": "fast"}]), patch("codex2api_image.cli.client_from_args", return_value=self.client), patch("codex2api_image.cli.run_prepared", side_effect=finish):
            code, stdout, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp, "--concurrency", "2"])
            self.assertEqual(code, 0)
            self.assertEqual([result["name"] for result in json.loads(stdout)["results"]], ["slow", "fast"])

    def test_batch_unknown_fields_and_invalid_booleans_fail(self):
        for row in ({"prompt": "a", "input_fidelity": "high"}, {"prompt": "a", "auto_retry": "true"}, {"prompt": "a", "images": [123]}):
            with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=[row]), patch("codex2api_image.cli.client_from_args") as client:
                code, _, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp])
                self.assertEqual(code, 1)
                client.assert_not_called()

    def test_existing_output_is_checked_before_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args") as client:
            path = Path(tmp) / "out.png"
            path.write_bytes(b"original")
            code, _, _ = invoke(["generate", "--prompt", "cat", "--out", str(path)])
            self.assertEqual(code, 1)
            client.assert_not_called()
            self.assertEqual(path.read_bytes(), b"original")

    def test_job_timeout_never_resubmits_accepted_job(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_with_retry", return_value=({"job": {"id": 9}}, [{"index": 1, "ok": True}])) as request, patch("codex2api_image.cli.wait_job", side_effect=CommandError("wait timed out", error_kind="job_timeout")):
            code, _, stderr = invoke(["job", "run", "--prompt", "cat", "--out", str(Path(tmp) / "out.png"), "--auto-retry"])
            self.assertEqual(code, 1)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(json.loads(stderr)["error"]["job_id"], 9)

    def test_partial_job_keeps_files_and_warning_but_returns_nonzero(self):
        job = {"id": 9, "status": "succeeded", "warning": "requested 4, completed 2", "params_json": json.dumps({"n": 4, "model": "gpt-image-2.5-flare"}),
               "assets": [{"cache_b64_json": encoded_image()}, {"cache_b64_json": encoded_image()}]}
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch("codex2api_image.cli.wait_job", return_value=job):
            code, stdout, _ = invoke(["job", "wait", "9", "--out", str(Path(tmp) / "out.png")])
            result = json.loads(stdout)
            self.assertEqual(code, 1)
            self.assertIn("completed 2", result["warning"])
            self.assertEqual((result["requested_n"], result["completed_n"]), (4, 2))
            self.assertEqual(len(list(Path(tmp).glob("*.png"))), 2)

    def test_dimension_mismatch_is_reported_without_local_resize(self):
        job = {"id": 9, "status": "succeeded", "params_json": json.dumps({"n": 1, "size": "1920x1080"}), "assets": [{"cache_b64_json": encoded_image()}]}
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch("codex2api_image.cli.wait_job", return_value=job):
            code, stdout, _ = invoke(["job", "wait", "9", "--out", str(Path(tmp) / "out.png")])
            result = json.loads(stdout)
            self.assertEqual(code, 1)
            self.assertEqual(result["images"][0]["width"], 16)
            self.assertIn("1920x1080", result["warning"])

    def test_generate_saves_and_reports_actual_image(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"data": [{"b64_json": encoded_image()}]}):
            code, stdout, stderr = invoke(["generate", "--prompt", "cat", "--out", str(Path(tmp) / "out.png")])
            result = json.loads(stdout)
            self.assertEqual((code, stderr), (0, ""))
            self.assertTrue(result["ok"])
            self.assertEqual(result["images"][0]["width"], 16)
            self.assertGreater(result["images"][0]["bytes"], 0)

    def test_invalid_timeouts_do_not_create_client(self):
        for timeout in ("0", "-1", "nan", "inf"):
            with patch("codex2api_image.cli.client_from_args") as client:
                code, _, _ = invoke(["models", "--timeout", timeout])
                self.assertEqual(code, 1)
                client.assert_not_called()

    def test_empty_batch_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=[]):
            code, _, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp, "--dry-run"])
            self.assertEqual(code, 1)

    def test_clean_background_can_only_be_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ["generate", "--prompt", "keep my subject", "--out", str(Path(tmp) / "out.png"), "--dry-run"]
            _, stdout, _ = invoke(args)
            self.assertEqual(json.loads(stdout)["payload"]["prompt"], "keep my subject")
            _, stdout, _ = invoke(args + ["--clean-background"])
            self.assertIn("User request: keep my subject", json.loads(stdout)["payload"]["prompt"])

    def test_expected_format_collision_is_checked_before_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args") as client:
            path = Path(tmp) / "out.jpg"
            path.write_bytes(b"original")
            code, _, _ = invoke(["generate", "--prompt", "cat", "--output-format", "jpeg", "--out", str(Path(tmp) / "out.png")])
            self.assertEqual(code, 1)
            client.assert_not_called()
            self.assertEqual(path.read_bytes(), b"original")

    def test_batch_different_extensions_cannot_resolve_to_same_file(self):
        rows = [{"prompt": "a", "out": "a.png", "output_format": "jpeg"}, {"prompt": "b", "out": "a.jpg", "output_format": "jpeg"}]
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.read_jobs", return_value=rows), patch("codex2api_image.cli.client_from_args") as client:
            code, _, _ = invoke(["batch", "--input", "mock.json", "--out-dir", tmp])
            self.assertEqual(code, 1)
            client.assert_not_called()

    def test_status_is_metadata_only_and_does_not_expose_request_data(self):
        job = sample_job("queued", 4, 0)
        job.update({"prompt": "test-only-key", "input_images": ["data:image/png;base64,private-image"],
                    "api_key_name": "private-key-name", "params_json": '{"api_key":"test-only-key"}'})
        with patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}) as request, patch.object(self.client, "get_binary") as download:
            code, stdout, stderr = invoke(["job", "status", "9"])
        result = json.loads(stdout)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual((result["job_id"], result["status"], result["requested_outputs"]), (9, "queued", 4))
        self.assertFalse(result["terminal"])
        self.assertEqual(request.call_args.args[:2], ("GET", "/images/jobs/9"))
        self.assertEqual(request.call_count, 1)
        download.assert_not_called()
        for private in ("test-only-key", "private-image", "private-key-name", "params_json", "input_images"):
            self.assertNotIn(private, stdout)

    def test_status_failure_preserves_classification_without_downloading(self):
        job = sample_job("failed", 4, 1)
        job.update({"error_kind": "image_postprocessing", "status_code": 500,
                    "error_message": "failed test-only-key", "upstream_body": "details test-only-key"})
        with patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}), patch.object(self.client, "get_binary") as download:
            code, stdout, _ = invoke(["job", "status", "9"])
        result = json.loads(stdout)
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["error_kind"], "image_postprocessing")
        self.assertEqual(result["error"]["next_command"], "job download 9")
        self.assertNotIn("test-only-key", stdout)
        self.assertNotIn("cache_b64_json", stdout)
        download.assert_not_called()

    def test_status_detects_partial_results_without_a_server_warning(self):
        job = sample_job("succeeded", 4, 2)
        with patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}):
            code, stdout, _ = invoke(["job", "status", "9"])
        result = json.loads(stdout)
        self.assertEqual(code, 1)
        self.assertIn("completed 2", result["warning"])
        self.assertTrue(result["terminal"])

    def test_download_terminal_job_only_fetches_existing_assets(self):
        for status in ("succeeded", "failed", "cancelled"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                job = sample_job(status)
                job["error_message"] = "stopped test-only-key"
                with patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}) as request, patch("codex2api_image.cli.wait_job") as wait:
                    code, stdout, stderr = invoke(["job", "download", "9", "--out", str(Path(tmp) / "saved.png")])
                result = json.loads(stdout)
                self.assertEqual(code, 0 if status == "succeeded" else 1)
                self.assertEqual(result["ok"], status == "succeeded")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["completed_n"], 1)
                self.assertEqual(Path(result["saved"][0]).read_bytes(), base64.b64decode(encoded_image()))
                self.assertEqual(stderr, "")
                self.assertNotIn("test-only-key", stdout)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(request.call_args.args[:2], ("GET", "/images/jobs/9"))
                wait.assert_not_called()

    def test_download_active_job_never_saves_partial_assets(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": sample_job("running", 4, 1)}) as request, patch("codex2api_image.cli.save_job_assets") as save:
            code, stdout, stderr = invoke(["job", "download", "9", "--out-dir", tmp])
            self.assertEqual((code, stdout), (1, ""))
            self.assertEqual(json.loads(stderr)["error"]["error_kind"], "job_active")
            self.assertEqual(list(Path(tmp).iterdir()), [])
            save.assert_not_called()
            self.assertEqual(request.call_count, 1)

    def test_download_failed_job_without_assets_preserves_original_error(self):
        job = sample_job("failed", 2, 0)
        job.update({"error_kind": "policy_specific_image", "status_code": 422, "error_message": "refused"})
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}), patch("codex2api_image.cli.save_job_assets") as save:
            code, _, stderr = invoke(["job", "download", "9", "--out-dir", tmp])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stderr)["error"]["error_kind"], "policy_specific_image")
        save.assert_not_called()

    def test_download_does_not_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args") as client:
            output = Path(tmp) / "saved.png"
            output.write_bytes(b"original")
            code, _, _ = invoke(["job", "download", "9", "--out", str(output)])
            self.assertEqual(code, 1)
            self.assertEqual(output.read_bytes(), b"original")
            client.assert_not_called()

    def test_progress_uses_stderr_json_lines_and_stdout_has_one_result(self):
        replies = [{"job": sample_job(status, 2, completed)} for status, completed in
                   (("queued", 0), ("queued", 0), ("running", 0), ("running", 1), ("running", 1), ("succeeded", 2))]
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", side_effect=replies) as request:
            code, stdout, stderr = invoke(["job", "run", "--prompt", "cat", "--n", "2", "--out-dir", tmp, "--poll-interval", "0.001"])
            result = json.loads(stdout)
            progress = [json.loads(line) for line in stderr.splitlines()]
            self.assertEqual(code, 0)
            self.assertEqual(result["completed_n"], 2)
            self.assertEqual([(p["status"], p["completed_outputs"]) for p in progress],
                             [("queued", 0), ("running", 0), ("running", 1), ("succeeded", 2)])
            self.assertTrue(all(p["event"] == "job_progress" and p["job_id"] == 9 for p in progress))
            self.assertEqual([c.args[0] for c in request.call_args_list].count("POST"), 1)

    def test_no_progress_keeps_stderr_empty_on_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": sample_job()}):
            code, stdout, stderr = invoke(["job", "wait", "9", "--out-dir", tmp, "--no-progress"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["ok"])

    def test_job_wait_and_download_do_not_share_the_submission_budget(self):
        now = [0.0]
        def finish(*args, **kwargs):
            now[0] = 5000.0
            return sample_job()
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": sample_job("queued", 1, 0)}), patch("codex2api_image.cli.time.monotonic", side_effect=lambda: now[0]), patch("codex2api_image.cli.wait_job", side_effect=finish) as wait, patch("codex2api_image.cli.save_job_assets", wraps=cli.save_job_assets) as save:
            code, stdout, stderr = invoke(["job", "run", "--prompt", "cat", "--out-dir", tmp, "--timeout", "1", "--queue-timeout", "2", "--execution-timeout", "3", "--download-timeout", "4"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["ok"])
        self.assertEqual(wait.call_args.kwargs["queue_timeout"], 2)
        self.assertEqual(wait.call_args.kwargs["execution_timeout"], 3)
        self.assertEqual(save.call_args.args[-1], 4)
        self.assertEqual(save.call_args.kwargs["request_timeout"], 1)

    def test_download_error_keeps_job_id_and_never_resubmits(self):
        job = sample_job()
        job["assets"] = [{"filename": "one.png", "proxy_url": "/p/img/1?sig=private"}]
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}) as request, patch.object(self.client, "get_binary", side_effect=CommandError("download failed", status_code=503)) as download:
            code, stdout, stderr = invoke(["job", "download", "9", "--out-dir", tmp, "--auto-retry"])
            self.assertEqual((code, stdout), (1, ""))
            error = json.loads(stderr)["error"]
            self.assertEqual((error["job_id"], error["phase"], error["next_command"]), (9, "download", "job download 9"))
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], "GET")
            self.assertEqual(download.call_count, 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_interrupted_wait_reports_resume_without_cancelling_or_resubmitting(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": sample_job("queued", 1, 0)}) as request, patch("codex2api_image.cli.wait_job", side_effect=KeyboardInterrupt):
            code, _, stderr = invoke(["job", "run", "--prompt", "cat", "--out-dir", tmp, "--auto-retry"])
        error = json.loads(stderr)["error"]
        self.assertEqual(code, 1)
        self.assertEqual(error["error_kind"], "job_interrupted")
        self.assertFalse(error["server_task_cancelled"])
        self.assertEqual(error["next_command"], "job wait 9")
        self.assertEqual(request.call_count, 1)

    def test_failed_wait_retains_recovery_hint_and_never_downloads_automatically(self):
        replies = [{"job": sample_job("queued", 4, 0)}, {"job": sample_job("cancelled", 4, 1)}]
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", side_effect=replies) as request, patch("codex2api_image.cli.save_job_assets") as save:
            code, stdout, stderr = invoke(["job", "run", "--prompt", "cat", "--n", "4", "--out-dir", tmp, "--auto-retry", "--no-progress"])
        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(json.loads(stderr)["error"]["next_command"], "job download 9")
        self.assertEqual([c.args[0] for c in request.call_args_list], ["POST", "GET"])
        save.assert_not_called()

    def test_invalid_job_budgets_and_ids_fail_before_loading_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            for option in ("--queue-timeout", "--execution-timeout", "--download-timeout", "--poll-interval"):
                for value in ("0", "-1", "nan", "inf"):
                    with self.subTest(option=option, value=value), patch("codex2api_image.cli.client_from_args") as client:
                        code, _, _ = invoke(["job", "wait", "9", "--out-dir", tmp, option, value])
                        self.assertEqual(code, 1)
                        client.assert_not_called()
            for command in ("status", "download", "wait"):
                for job_id in ("0", "-1"):
                    with self.subTest(command=command, job_id=job_id), patch("codex2api_image.cli.client_from_args") as client:
                        args = ["job", command, job_id] + ([] if command == "status" else ["--out-dir", tmp])
                        code, _, _ = invoke(args)
                        self.assertEqual(code, 1)
                        client.assert_not_called()

    def test_submit_reports_progress_metadata_without_params(self):
        job = sample_job("queued", 4, 0)
        with patch("codex2api_image.cli.client_from_args", return_value=self.client), patch.object(self.client, "request_json", return_value={"job": job}) as request:
            code, stdout, stderr = invoke(["job", "submit", "--prompt", "cat", "--n", "4"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["requested_outputs"], 4)
        self.assertNotIn("params_json", stdout)
        self.assertEqual(request.call_count, 1)

    def test_job_dry_run_accepts_new_budgets_without_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args") as client:
            code, stdout, stderr = invoke(["job", "run", "--prompt", "cat", "--out-dir", tmp, "--queue-timeout", "2", "--execution-timeout", "3", "--download-timeout", "4", "--no-progress", "--dry-run"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["dry_run"])
        client.assert_not_called()

    def test_batch_job_progress_keeps_ids_and_final_input_order(self):
        rows = [{"mode": "job", "prompt": prompt, "out": prompt + ".png"} for prompt in ("first", "second")]
        def reply(method, path, payload=None, **kwargs):
            if method == "POST":
                assert payload is not None
                job = sample_job("queued", 1, 0)
                job["id"] = 9 if payload["prompt"] == "first" else 10
            else:
                job = sample_job()
                job["id"] = int(path.rsplit("/", 1)[-1])
            return {"job": job}
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.cli.client_from_args", return_value=self.client), patch("codex2api_image.cli.read_jobs", return_value=rows), patch.object(self.client, "request_json", side_effect=reply) as request:
            code, stdout, stderr = invoke(["batch", "--input", "mock.json", "--out-dir", tmp, "--concurrency", "2", "--queue-timeout", "1", "--execution-timeout", "2", "--download-timeout", "3"])
            result = json.loads(stdout)
            progress = [json.loads(line) for line in stderr.splitlines()]
            self.assertEqual(code, 0)
            self.assertEqual(result["failed"], 0)
            self.assertEqual([r["job_id"] for r in result["results"]], [9, 10])
            self.assertEqual({p["job_id"] for p in progress}, {9, 10})
            self.assertEqual([c.args[0] for c in request.call_args_list].count("POST"), 2)
            self.assertEqual(len(list(Path(tmp).glob("*.png"))), 2)

    def test_default_job_budgets_are_separate_and_progress_can_be_disabled(self):
        parser = cli.build_parser()
        for args in (["job", "wait", "9", "--out-dir", "new-output"],
                     ["job", "run", "--prompt", "cat", "--out-dir", "new-output"],
                     ["batch", "--input", "mock.json", "--out-dir", "new-output"]):
            parsed = parser.parse_args(args)
            self.assertEqual((parsed.timeout, parsed.queue_timeout, parsed.download_timeout), (900, 900, 900))
            self.assertIsNone(parsed.execution_timeout)
            self.assertTrue(parsed.progress)
            self.assertFalse(parser.parse_args(args + ["--no-progress"]).progress)

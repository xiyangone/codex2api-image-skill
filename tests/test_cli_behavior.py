import base64
import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from codex2api_image import cli
from codex2api_image.config import RuntimeConfig
from codex2api_image.errors import CommandError
from codex2api_image.http_client import Codex2APIClient


def invoke(args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def encoded_image():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


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

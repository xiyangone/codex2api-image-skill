import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from codex2api_image.config import RuntimeConfig
from codex2api_image.errors import CommandError
from codex2api_image.http_client import Codex2APIClient
from codex2api_image.jobs import get_job, response_job_id, save_job_assets, wait_job


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, delay):
        self.now += delay


def inline_image():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


class JobTests(unittest.TestCase):
    def setUp(self):
        self.client = Codex2APIClient(RuntimeConfig("http://127.0.0.1:8080/v1", "private-test-key"))

    def test_polling_defaults_to_metadata_only(self):
        with patch.object(self.client, "request_json", return_value={"job": {}}) as request:
            get_job(self.client, 7, 1)
            self.assertEqual(request.call_args.args[1], "/images/jobs/7")

    def test_job_ids_are_positive_integers(self):
        for value in (0, -1, True, "1"):
            with self.assertRaises(CommandError):
                response_job_id({"job": {"id": value}})

    def test_polling_stops_at_deadline_and_keeps_id(self):
        clock = Clock()
        with patch.object(self.client, "request_json", return_value={"job": {"id": 7, "status": "running"}}) as request, self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 1, 0.4, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 1)
        self.assertEqual(caught.exception.job_id, 7)
        self.assertEqual(caught.exception.error_kind, "job_timeout")
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_success_preserves_warning(self):
        job = {"id": 7, "status": "succeeded", "warning": "requested 4, completed 2"}
        with patch.object(self.client, "request_json", return_value={"job": job}):
            self.assertEqual(wait_job(self.client, 7, 1, 0.01)["warning"], job["warning"])

    def test_failure_keeps_server_classification_and_sanitizes_body(self):
        job = {"id": 7, "status": "failed", "status_code": 422, "error_kind": "policy_specific_image",
               "error_message": "refused private-test-key", "upstream_body": "details private-test-key"}
        with patch.object(self.client, "request_json", return_value={"job": job}), self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 1, 0.01)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.error_kind, "policy_specific_image")
        self.assertNotIn("private-test-key", str(caught.exception.as_dict()))

    def test_unknown_status_is_not_polled_forever(self):
        with patch.object(self.client, "request_json", return_value={"job": {"status": "unrecognized"}}), self.assertRaises(CommandError):
            wait_job(self.client, 1, 1, 0.01)

    def test_server_filename_escape_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(self.client, "get_binary") as download:
            job = {"id": 7, "assets": [{"filename": "../escape.png", "proxy_url": "/p/img/1"}]}
            with self.assertRaises(CommandError):
                save_job_assets(self.client, job, None, Path(tmp), 1)
            download.assert_not_called()

    def test_multiple_assets_honor_explicit_out_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = {"id": 7, "assets": [{"cache_b64_json": inline_image()}, {"cache_b64_json": inline_image()}]}
            saved = save_job_assets(self.client, job, Path(tmp) / "named.png", None, 1)
            self.assertEqual([path.name for path in saved], ["named-001.png", "named-002.png"])
            self.assertTrue(all(path.parent == Path(tmp) for path in saved))

    def test_all_asset_paths_are_checked_before_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(self.client, "get_binary") as download:
            job = {"id": 7, "assets": [{"filename": "a.png", "proxy_url": "/p/img/1"}, {"filename": "A.png", "proxy_url": "/p/img/2"}]}
            with self.assertRaises(CommandError):
                save_job_assets(self.client, job, None, Path(tmp), 1)
            download.assert_not_called()

    def test_output_location_is_required(self):
        with self.assertRaises(CommandError):
            save_job_assets(self.client, {"assets": []}, None, None, 1)

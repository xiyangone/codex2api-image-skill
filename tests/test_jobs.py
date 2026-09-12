import base64
import io
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from PIL import Image

from codex2api_image.config import RuntimeConfig
from codex2api_image.errors import CommandError
from codex2api_image.http_client import Codex2APIClient
from codex2api_image.jobs import get_job, job_snapshot, response_job_id, save_job_assets, wait_job


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
        job = {"id": 7, "status": "queued"}
        with patch.object(self.client, "request_json", return_value={"job": job}) as request:
            self.assertEqual(get_job(self.client, 7, 1), job)
            self.assertEqual(request.call_args.args[1], "/images/jobs/7")

    def test_job_ids_are_positive_integers(self):
        for value in (0, -1, True, "1"):
            with self.assertRaises(CommandError):
                response_job_id({"job": {"id": value}})

    def test_polling_stops_at_deadline_and_keeps_id(self):
        clock = Clock()
        with patch.object(self.client, "request_json", return_value={"job": {"id": 7, "status": "running"}}) as request, self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 1, 0.4, execution_timeout=1, clock=clock, sleep=clock.sleep)
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
        with patch.object(self.client, "request_json", return_value={"job": {"id": 1, "status": "unrecognized"}}), self.assertRaises(CommandError):
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

    def test_queue_and_execution_have_independent_budgets(self):
        clock = Clock()
        replies = [{"job": {"id": 7, "status": status}} for status in
                   ("queued", "queued", "running", "running", "succeeded")]
        with patch.object(self.client, "request_json", side_effect=replies) as request:
            job = wait_job(self.client, 7, 0.05, 0.4, queue_timeout=1, execution_timeout=1,
                           clock=clock, sleep=clock.sleep)
        self.assertEqual(job["status"], "succeeded")
        self.assertAlmostEqual(clock.now, 1.6)
        self.assertTrue(all(0 < call.kwargs["timeout"] <= 0.05 + 1e-9 for call in request.call_args_list))

    def test_queue_timeout_preserves_progress_and_resume_instruction(self):
        clock = Clock()
        job = {"id": 7, "status": "queued", "requested_outputs": 4, "completed_outputs": 0}
        with patch.object(self.client, "request_json", return_value={"job": job}) as request, self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 2, 0.4, queue_timeout=1, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 1)
        error = caught.exception.as_dict()
        self.assertEqual((error["job_id"], error["phase"], error["requested_outputs"]), (7, "queue", 4))
        self.assertFalse(error["server_task_cancelled"])
        self.assertEqual(error["next_command"], "job wait 7")
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_execution_budget_scales_with_server_requested_count(self):
        clock = Clock()
        job = {"id": 7, "status": "running", "requested_outputs": 4, "params_json": '{"n":1}'}
        with patch.object(self.client, "request_json", return_value={"job": job}), self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 1, 500, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 3600)
        self.assertEqual(caught.exception.details["phase"], "execution")

    def test_progress_and_stale_queue_state_do_not_extend_execution(self):
        clock = Clock()
        def reply(*args, **kwargs):
            status = "queued" if 0.3 < clock.now < 0.5 else "running"
            return {"job": {"id": 7, "status": status, "requested_outputs": 4, "completed_outputs": int(clock.now * 2)}}
        with patch.object(self.client, "request_json", side_effect=reply), self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 1, 0.4, execution_timeout=1, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 1)
        self.assertEqual(caught.exception.details["phase"], "execution")

    def test_progress_emits_only_changed_status_or_counts(self):
        clock = Clock()
        updates = []
        replies = [{"job": {"id": 7, "status": status, "requested_outputs": 2, "completed_outputs": completed,
                            "prompt": "private-test-key", "api_key_masked": "not-for-progress"}}
                   for status, completed in (("queued", 0), ("queued", 0), ("running", 0), ("running", 1),
                                              ("running", 1), ("succeeded", 2))]
        with patch.object(self.client, "request_json", side_effect=replies):
            wait_job(self.client, 7, 1, 0.1, on_progress=updates.append, clock=clock, sleep=clock.sleep)
        self.assertEqual([(item["status"], item["completed_outputs"]) for item in updates],
                         [("queued", 0), ("running", 0), ("running", 1), ("succeeded", 2)])
        self.assertNotIn("private-test-key", str(updates))
        self.assertNotIn("api_key_masked", str(updates))

    def test_cancelled_job_stops_and_exposes_existing_asset_recovery(self):
        job = {"id": 7, "status": "cancelled", "requested_outputs": 4, "completed_outputs": 1,
               "assets": [{"cache_b64_json": inline_image()}]}
        with patch.object(self.client, "request_json", return_value={"job": job}) as request, self.assertRaises(CommandError) as caught:
            wait_job(self.client, 7, 1, 0.1, auto_retry=True)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(caught.exception.details["next_command"], "job download 7")
        self.assertEqual(caught.exception.details["asset_count"], 1)
        self.assertEqual(caught.exception.error_kind, "cancelled")

    def test_invalid_read_id_fails_before_request_and_response_ids_must_match(self):
        for job_id in (0, -1, True, "7", None):
            with patch.object(self.client, "request_json") as request, self.assertRaises(CommandError):
                get_job(self.client, cast(Any, job_id), 1)
            request.assert_not_called()
        for response in ({"job": {"id": 8, "status": "queued"}}, {"job": {}}, {"job": []}):
            with patch.object(self.client, "request_json", return_value=response), self.assertRaises(CommandError) as caught:
                get_job(self.client, 7, 1)
            self.assertEqual(caught.exception.job_id, 7)
            self.assertFalse(caught.exception.outcome_unknown)

    def test_job_progress_rejects_malformed_counters_and_assets(self):
        invalid = [{"requested_outputs": n} for n in (0, 5, True, None, "2")]
        invalid += [{"completed_outputs": n} for n in (-1, True, "1", None)]
        invalid += [{"assets": assets} for assets in ({}, False, "", [1])]
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(CommandError):
                job_snapshot({"id": 7, "status": "queued", **override})

    def test_request_parameters_do_not_infer_requested_count_from_saved_assets(self):
        snapshot = job_snapshot({"id": 7, "status": "succeeded", "params_json": '{"n":4}', "assets": [{}]})
        self.assertEqual((snapshot["requested_outputs"], snapshot["completed_outputs"]), (4, 1))

    def test_reads_use_bounded_opt_in_retries(self):
        clock = Clock()
        responses = [CommandError("busy", status_code=503, retry_after=0.1), {"job": {"id": 7, "status": "queued"}}]
        with patch.object(self.client, "request_json", side_effect=responses) as request:
            job = get_job(self.client, 7, 1, auto_retry=True, clock=clock, sleep=clock.sleep)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        self.assertAlmostEqual(clock.now, 0.2)

    def test_download_budget_is_shared_across_assets_not_reset_per_file(self):
        clock = Clock()
        job = {"id": 7, "assets": [{"filename": f"{i}.png", "proxy_url": f"/p/img/{i}"} for i in (1, 2)]}
        def fetch(*args, **kwargs):
            clock.sleep(0.6)
            return base64.b64decode(inline_image())
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.jobs.asset_bytes", side_effect=fetch) as download, self.assertRaises(CommandError) as caught:
            save_job_assets(self.client, job, None, Path(tmp), 1, clock=clock)
        self.assertEqual(caught.exception.job_id, 7)
        self.assertEqual(caught.exception.details["phase"], "download")
        self.assertEqual(caught.exception.details["next_command"], "job download 7")
        self.assertAlmostEqual(download.call_args_list[0].args[2], 1)
        self.assertAlmostEqual(download.call_args_list[1].args[2], 0.4)

    def test_each_asset_request_is_capped_by_http_timeout(self):
        clock = Clock()
        job = {"id": 7, "assets": [{"filename": f"{i}.png", "proxy_url": f"/p/img/{i}"} for i in (1, 2)]}
        def fetch(*args, **kwargs):
            clock.sleep(0.2)
            return base64.b64decode(inline_image())
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.jobs.asset_bytes", side_effect=fetch) as download:
            saved = save_job_assets(self.client, job, None, Path(tmp), 2, request_timeout=0.3, clock=clock)
            self.assertEqual(len(saved), 2)
            self.assertTrue(all(path.is_file() for path in saved))
        self.assertEqual([c.args[2] for c in download.call_args_list], [0.3, 0.3])

    def test_download_failure_keeps_recovery_context(self):
        job = {"id": 7, "assets": [{"filename": "one.png", "proxy_url": "/p/img/1"}]}
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.jobs.asset_bytes", side_effect=CommandError("expired", status_code=403)), self.assertRaises(CommandError) as caught:
            save_job_assets(self.client, job, None, Path(tmp), 1)
        self.assertEqual(caught.exception.job_id, 7)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.details["next_command"], "job download 7")

    def test_auth_failure_is_not_retried_and_read_does_not_claim_unknown_post_outcome(self):
        with patch.object(self.client, "request_json", side_effect=CommandError("unauthorized", status_code=401)) as request, self.assertRaises(CommandError) as caught:
            get_job(self.client, 7, 1, auto_retry=True)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(caught.exception.job_id, 7)
        self.assertEqual(caught.exception.route, "/images/jobs/7")
        self.assertFalse(caught.exception.outcome_unknown)

    def test_invalid_wait_budgets_are_rejected_before_request(self):
        for name in ("queue_timeout", "execution_timeout"):
            for value in (0, -1, float("inf"), float("nan"), True):
                with self.subTest(name=name, value=value), patch.object(self.client, "request_json") as request, self.assertRaises(CommandError):
                    if name == "queue_timeout":
                        wait_job(self.client, 7, 1, 0.1, queue_timeout=value)
                    else:
                        wait_job(self.client, 7, 1, 0.1, execution_timeout=value)
                request.assert_not_called()

    def test_malformed_params_do_not_silently_guess_output_count(self):
        for raw in ("broken", "[]", "null", 123):
            with self.subTest(raw=raw), self.assertRaises(CommandError):
                job_snapshot({"id": 7, "status": "succeeded", "params_json": raw, "assets": [{}]})

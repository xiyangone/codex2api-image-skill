import io
import json
import unittest
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from codex2api_image.config import RuntimeConfig
from codex2api_image.errors import CommandError
from codex2api_image.http_client import Codex2APIClient, NoRedirect, parse_retry_after


class Response:
    def __init__(self, data=b'{"data":[]}'):
        self.data = data
        self.headers = {"Content-Type": "application/json"}
        self.status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, count):
        return self.data[:count]


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class HTTPClientTests(unittest.TestCase):
    def setUp(self):
        self.client = Codex2APIClient(RuntimeConfig("http://127.0.0.1:8080/v1", "local-secret"))

    def test_api_paths_and_signed_assets(self):
        self.assertEqual(self.client.api_url("/images/jobs"), "http://127.0.0.1:8080/v1/images/jobs")
        self.assertEqual(self.client.service_url("/p/img/1?sig=test"), "http://127.0.0.1:8080/p/img/1?sig=test")
        with self.assertRaises(CommandError):
            self.client.api_url("https://other.example/v1")

    def test_credential_is_only_sent_to_api(self):
        with patch.object(self.client, "_open", return_value=Response()) as opened:
            self.client.request_json("GET", "/models")
            self.assertEqual(opened.call_args.args[0].get_header("Authorization"), "Bearer local-secret")
            self.client.get_binary("https://assets.example/image.png")
            self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))

    def test_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(Request("https://one.test"), None, 302, "Found", {}, "https://two.test"))

    def test_non_http_asset_schemes_fail(self):
        for url in ("file:///secret", "ftp://example.test/image", "//example.test/image", "https://user:secret@example.test/image"):
            with self.subTest(url=url), self.assertRaises(CommandError):
                self.client.get_binary(url)

    def test_http_error_preserves_structure_and_redacts_key(self):
        body = json.dumps({"error": {"code": "rate_limit_reached", "message": "retry local-secret", "api_key": "other-secret"}}).encode()
        error = HTTPError("http://localhost", 429, "slow down", {"Retry-After": "3"}, io.BytesIO(body))  # type: ignore[arg-type]
        with patch.object(self.client, "_open", side_effect=error), self.assertRaises(CommandError) as caught:
            self.client.request_json("POST", "/images/generations", {})
        data = caught.exception.as_dict()
        self.assertEqual(data["status_code"], 429)
        self.assertEqual(data["retry_after"], 3)
        self.assertEqual(data["error_kind"], "rate_limit_reached")
        self.assertNotIn("local-secret", json.dumps(data))
        self.assertNotIn("other-secret", json.dumps(data))

    def test_retry_after_seconds_date_and_invalid_values(self):
        self.assertEqual(parse_retry_after("12"), 12)
        self.assertEqual(parse_retry_after("Thu, 01 Jan 1970 00:00:20 GMT", now=5), 15)
        for raw in ("bad", "nan", "inf", ""):
            self.assertIsNone(parse_retry_after(raw))

    def test_authorized_retry_keeps_payload_identical_and_waits(self):
        clock = Clock()
        payload = {"prompt": "A small orange cat", "quality": "high"}
        error = CommandError("busy", status_code=503, retry_after=2)
        with patch.object(self.client, "request_json", side_effect=[error, {"data": []}]) as request:
            _, attempts = self.client.request_with_retry("POST", "/images/generations", payload, timeout=10,
                                                        auto_retry=True, clock=clock, sleep=clock.sleep)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(clock.sleeps, [2])
        self.assertIs(request.call_args_list[0].args[2], payload)
        self.assertIs(request.call_args_list[1].args[2], payload)

    def test_default_does_not_retry(self):
        error = CommandError("busy", status_code=503, retry_after=1)
        with patch.object(self.client, "request_json", side_effect=error) as request, self.assertRaises(CommandError):
            self.client.request_with_retry("POST", "/images/jobs", {}, timeout=10)
        self.assertEqual(request.call_count, 1)

    def test_permanent_and_ambiguous_post_failures_never_retry(self):
        for error in (CommandError("invalid key", status_code=401),
                      CommandError("refused", status_code=422, error_kind="image_output_rejected"),
                      CommandError("policy", status_code=503, error_kind="policy_specific_image", retry_after=1),
                      CommandError("timeout", error_kind="transport_error", outcome_unknown=True),
                      CommandError("unclassified server failure", status_code=503)):
            with self.subTest(error=error), patch.object(self.client, "request_json", side_effect=error) as request, self.assertRaises(CommandError):
                self.client.request_with_retry("POST", "/images/jobs", {}, timeout=10, auto_retry=True)
            self.assertEqual(request.call_count, 1)

    def test_retry_after_cannot_exceed_budget(self):
        clock = Clock()
        with patch.object(self.client, "request_json", side_effect=CommandError("busy", status_code=429, retry_after=30)) as request, self.assertRaises(CommandError):
            self.client.request_with_retry("POST", "/images/jobs", {}, timeout=10, auto_retry=True, clock=clock, sleep=clock.sleep)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(clock.sleeps, [])

    def test_transport_timeout_reports_unknown_post_outcome(self):
        with patch.object(self.client, "_open", side_effect=TimeoutError("connection timeout")), self.assertRaises(CommandError) as caught:
            self.client.request_json("POST", "/images/jobs", {})
        self.assertTrue(caught.exception.outcome_unknown)

    def test_invalid_json_is_not_retried_as_policy_rejection(self):
        with patch.object(self.client, "_open", return_value=Response(b"not json")), self.assertRaises(CommandError) as caught:
            self.client.request_json("POST", "/images/generations", {})
        self.assertEqual(caught.exception.error_kind, "invalid_response")
        self.assertTrue(caught.exception.outcome_unknown)

    def test_response_size_limit(self):
        with patch("codex2api_image.http_client.MAX_JSON_BYTES", 3), patch.object(self.client, "_open", return_value=Response(b"1234")), self.assertRaises(CommandError):
            self.client.request_json("GET", "/models")

    def test_incomplete_http_body_is_structured_and_not_resubmitted(self):
        response = Response()
        with patch.object(response, "read", side_effect=IncompleteRead(b"partial", 10)), patch.object(self.client, "_open", return_value=response), self.assertRaises(CommandError) as caught:
            self.client.request_json("POST", "/images/jobs", {})
        self.assertEqual(caught.exception.error_kind, "transport_error")
        self.assertTrue(caught.exception.outcome_unknown)

    def test_invalid_json_retains_http_status(self):
        with patch.object(self.client, "_open", return_value=Response(b"[]")), self.assertRaises(CommandError) as caught:
            self.client.request_json("GET", "/models")
        self.assertEqual(caught.exception.status_code, 200)
        self.assertEqual(caught.exception.body, "[]")

    def test_retry_attempt_limit(self):
        clock = Clock()
        with patch.object(self.client, "request_json", side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(CommandError("busy", status_code=503, retry_after=1))) as request, self.assertRaises(CommandError):
            self.client.request_with_retry("POST", "/images/jobs", {}, timeout=20, auto_retry=True, max_attempts=3, clock=clock, sleep=clock.sleep)
        self.assertEqual(request.call_count, 3)

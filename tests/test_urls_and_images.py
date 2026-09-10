import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from codex2api_image.config import RuntimeConfig
from codex2api_image.errors import CommandError
from codex2api_image.http_client import BinaryResponse, Codex2APIClient
from codex2api_image.image_info import detect_image_bytes, image_info
from codex2api_image.images import data_url_bytes, image_source_to_url, save_bytes, save_response_images
from codex2api_image.paths import contained_output, ensure_distinct_outputs, output_targets


def picture(kind="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (12, 8), (100, 120, 140)).save(stream, format=kind)
    return stream.getvalue()


class URLAndImageTests(unittest.TestCase):
    def setUp(self):
        self.client = Codex2APIClient(RuntimeConfig("http://127.0.0.1:8080/v1", "test"))

    def test_full_decode_for_supported_formats(self):
        for kind in ("PNG", "JPEG", "WEBP", "GIF"):
            with self.subTest(kind=kind):
                self.assertEqual(detect_image_bytes(picture(kind)), (kind.lower(), 12, 8))

    def test_header_only_and_truncated_files_fail(self):
        for data in (picture()[:24], picture()[:-16], b"not an image"):
            with self.assertRaises(CommandError):
                detect_image_bytes(data)

    def test_invalid_base64_fails(self):
        with self.assertRaises(CommandError):
            data_url_bytes("data:image/png;base64,@@@")

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "existing.png"
            path.write_bytes(b"original")
            with self.assertRaises(CommandError):
                save_bytes(picture(), path)
            self.assertEqual(path.read_bytes(), b"original")

    def test_exclusive_creation_handles_race(self):
        with tempfile.TemporaryDirectory() as tmp, patch("codex2api_image.images.resolve_output", side_effect=lambda p: p):
            path = Path(tmp) / "race.png"
            path.write_bytes(b"original")
            with self.assertRaises(CommandError):
                save_bytes(picture(), path)
            self.assertEqual(path.read_bytes(), b"original")

    def test_actual_format_controls_extension_without_reencoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = picture("JPEG")
            saved = save_bytes(data, Path(tmp) / "image.png")
            self.assertEqual(saved.suffix, ".jpg")
            self.assertEqual(saved.read_bytes(), data)
            self.assertEqual(image_info(saved)["bytes"], len(data))

    def test_all_returned_images_are_saved(self):
        encoded = base64.b64encode(picture()).decode()
        with tempfile.TemporaryDirectory() as tmp:
            paths = save_response_images(self.client, {"data": [{"b64_json": encoded}, {"b64_json": encoded}]}, Path(tmp) / "out.png", 1)
            self.assertEqual([path.name for path in paths], ["out-001.png", "out-002.png"])
            self.assertTrue(all(path.is_file() for path in paths))

    def test_whole_image_set_validated_before_writing(self):
        good = base64.b64encode(picture()).decode()
        bad = base64.b64encode(b"broken").decode()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CommandError):
                save_response_images(self.client, {"data": [{"b64_json": good}, {"b64_json": bad}]}, Path(tmp) / "out.png", 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_output_traversal_and_absolute_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("../escape.png", "..\\escape.png", "C:\\escape.png", "\\\\server\\share\\out.png", "out.png:stream", "CON.png", "foo. /out.png"):
                with self.subTest(name=name), self.assertRaises(CommandError):
                    contained_output(Path(tmp), name)

    def test_duplicate_paths_and_generated_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(CommandError):
                ensure_distinct_outputs([root / "a.png", root / "A.png"])
            self.assertEqual(len(output_targets(root / "a.png", 4)), 4)

    def test_local_reference_uses_detected_mime(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrong.jpg"
            path.write_bytes(picture("PNG"))
            self.assertTrue(image_source_to_url(str(path)).startswith("data:image/png;base64,"))
            self.assertTrue(image_source_to_url(path.as_uri()).startswith("data:image/png;base64,"))

    def test_input_size_limit(self):
        with patch("codex2api_image.images.MAX_INPUT_BYTES", 1), self.assertRaises(CommandError):
            image_source_to_url("data:image/png;base64," + base64.b64encode(picture()).decode())

    def test_output_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "real"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink not available in this test session: {error}")
            with self.assertRaises(CommandError):
                save_bytes(picture(), link / "out.png")

    def test_missing_image_response_preserves_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(CommandError) as caught:
            save_response_images(self.client, {"message": "upstream returned text"}, Path(tmp) / "out.png", 1)
        self.assertIn("upstream returned text", caught.exception.body)
        self.assertEqual(caught.exception.error_kind, "invalid_response")

    def test_url_results_download_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(self.client, "get_binary", return_value=BinaryResponse(picture(), "image/png")) as download:
            saved = save_response_images(self.client, {"data": [{"url": "/p/img/1?sig=fake"}]}, Path(tmp) / "out.png", 1)
            self.assertEqual(len(saved), 1)
            download.assert_called_once()

    def test_link_guard_without_symlink_creation_privilege(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.png"
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(CommandError):
                save_bytes(picture(), path)
            self.assertFalse(path.exists())

    def test_explicit_output_rejects_windows_device_names(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(CommandError):
            save_bytes(picture(), Path(tmp) / "NUL.png")

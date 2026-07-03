import base64
import tempfile
import unittest
from pathlib import Path


class URLAndImageTests(unittest.TestCase):
    def test_api_and_service_urls(self) -> None:
        from codex2api_image.config import RuntimeConfig
        from codex2api_image.http_client import Codex2APIClient

        client = Codex2APIClient(RuntimeConfig(base_url="http://127.0.0.1:8080/v1", api_key="sk-test"))
        self.assertEqual(client.api_url("/images/generations"), "http://127.0.0.1:8080/v1/images/generations")
        self.assertEqual(client.service_url("/p/img/1?sig=x"), "http://127.0.0.1:8080/p/img/1?sig=x")

    def test_png_dimensions(self) -> None:
        from codex2api_image.image_info import image_dimensions

        png_1x1 = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.png"
            path.write_bytes(png_1x1)
            kind, width, height = image_dimensions(path)
            self.assertEqual((kind, width, height), ("png", 1, 1))


if __name__ == "__main__":
    unittest.main()

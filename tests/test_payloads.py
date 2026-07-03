import unittest


class PayloadTests(unittest.TestCase):
    def test_clean_background_prompt_is_explicit_and_preserves_user_request(self) -> None:
        from codex2api_image.payloads import clean_background_prompt

        prompt = clean_background_prompt("remove the background")

        self.assertIn("plain clean light background", prompt)
        self.assertIn("not transparent", prompt.lower())
        self.assertIn("User request: remove the background", prompt)

    def test_generation_payload_omits_size_when_requested(self) -> None:
        from codex2api_image.payloads import ImageOptions, generation_payload

        payload = generation_payload(
            prompt="draw",
            options=ImageOptions(model="gpt-image-2", size="omit", quality="high", output_format="png"),
        )
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["prompt"], "draw")
        self.assertEqual(payload["quality"], "high")
        self.assertEqual(payload["output_format"], "png")
        self.assertNotIn("size", payload)

    def test_edit_payload_uses_images_array(self) -> None:
        from codex2api_image.payloads import ImageOptions, edit_payload

        payload = edit_payload(
            prompt="edit",
            images=("data:image/png;base64,AAAA",),
            options=ImageOptions(model="gpt-image-2"),
        )
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["images"], [{"image_url": "data:image/png;base64,AAAA"}])
        self.assertNotIn("input_fidelity", payload)


if __name__ == "__main__":
    unittest.main()

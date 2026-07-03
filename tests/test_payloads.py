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

    def test_auto_retry_attempts_include_prompt_and_parameter_fallbacks(self) -> None:
        from codex2api_image.payloads import ImageOptions, auto_retry_attempts

        attempts = auto_retry_attempts(
            "upscale this specific image while preserving identity",
            ImageOptions(model="gpt-image-2-4k", size="2048x2048", quality="high", output_format="png", upscale="4k"),
        )

        reasons = [reason for reason, _, _ in attempts]
        self.assertEqual(
            reasons,
            [
                "original",
                "sanitized_user_prompt",
                "t1_conservative_restoration_prompt",
                "t2_conservative_quality_cleanup_prompt",
                "t3_modest_outfit_reframe_prompt",
                "png_to_jpeg",
                "quality_auto",
                "quality_low",
                "lower_resolution_default",
            ],
        )
        lowered = attempts[-1][1]
        self.assertEqual(lowered.model, "gpt-image-2")
        self.assertEqual(lowered.size, "auto")
        self.assertEqual(lowered.quality, "auto")
        self.assertEqual(lowered.output_format, "jpeg")
        self.assertEqual(lowered.upscale, "")

    def test_auto_retry_policy_prompts_avoid_specific_person_rejection_terms(self) -> None:
        from codex2api_image.payloads import ImageOptions, auto_retry_attempts

        attempts = auto_retry_attempts(
            "upscale this specific image while preserving identity, face, and body shape",
            ImageOptions(model="gpt-image-2", output_format="png"),
        )

        policy_prompts = [attempt_prompt.lower() for reason, _, attempt_prompt in attempts[1:5]]
        forbidden = [
            "upscale this specific image",
            "edit this particular image",
            "preserve identity",
            "identity",
            "person",
            "face",
            "body shape",
            "version",
        ]
        for attempt_prompt in policy_prompts:
            for term in forbidden:
                self.assertNotIn(term, attempt_prompt)
        self.assertIn("sanitized user request", policy_prompts[0])
        self.assertIn("conservative photo restoration pass", policy_prompts[1])
        self.assertIn("conservative quality cleanup", policy_prompts[2])
        self.assertNotIn("high-resolution finished version", policy_prompts[2])
        self.assertNotIn("create a new version", policy_prompts[2])

    def test_auto_retry_parameter_downgrades_happen_after_policy_prompts(self) -> None:
        from codex2api_image.payloads import ImageOptions, auto_retry_attempts

        reasons = [
            reason
            for reason, _, _ in auto_retry_attempts(
                "enhance",
                ImageOptions(model="gpt-image-2-4k", quality="high", output_format="png", upscale="4k"),
            )
        ]

        self.assertLess(reasons.index("t2_conservative_quality_cleanup_prompt"), reasons.index("png_to_jpeg"))
        self.assertLess(reasons.index("t3_modest_outfit_reframe_prompt"), reasons.index("png_to_jpeg"))
        self.assertLess(reasons.index("t2_conservative_quality_cleanup_prompt"), reasons.index("quality_auto"))
        self.assertLess(reasons.index("t2_conservative_quality_cleanup_prompt"), reasons.index("lower_resolution_default"))
        self.assertNotIn("t3_production_artwork_prompt", reasons)
        self.assertNotIn("t4_illustrated_final_render_prompt", reasons)

    def test_auto_retry_adds_closed_foot_tights_prompt_for_white_tights_requests(self) -> None:
        from codex2api_image.payloads import ImageOptions, auto_retry_attempts

        attempts = auto_retry_attempts(
            "裸足白丝手机壁纸",
            ImageOptions(model="gpt-image-2-4k", size="1024x1792", quality="high", output_format="png"),
        )

        reasons = [reason for reason, _, _ in attempts]
        self.assertIn("t4_closed_foot_tights_reframe_prompt", reasons)
        prompt = dict((reason, attempt_prompt) for reason, _, attempt_prompt in attempts)["t4_closed_foot_tights_reframe_prompt"].lower()
        self.assertIn("smooth closed-foot opaque white tights", prompt)
        self.assertIn("continuous soft fabric", prompt)
        self.assertIn("not toe-sock styling", prompt)
        self.assertNotIn("barefoot", prompt)
        self.assertNotIn("toes", prompt)


if __name__ == "__main__":
    unittest.main()

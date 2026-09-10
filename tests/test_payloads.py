import unittest
from dataclasses import replace

from codex2api_image.errors import CommandError
from codex2api_image.payloads import ImageOptions, clean_background_prompt, edit_payload, generation_payload, job_payload


class PayloadTests(unittest.TestCase):
    def test_new_models_quality_and_snapshots_pass_through(self):
        for model in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst-4k", "gpt-image-2.5-flare-2026-09-09-2k"):
            for quality in ("xhigh", "max"):
                with self.subTest(model=model, quality=quality):
                    payload = generation_payload("draw a cat", ImageOptions(model=model, quality=quality))
                    self.assertEqual(payload["model"], model)
                    self.assertEqual(payload["quality"], quality)

    def test_default_image_model_stays_image_2(self):
        self.assertEqual(generation_payload("cat", ImageOptions())["model"], "gpt-image-2")

    def test_legacy_model_does_not_silently_downgrade_quality(self):
        for model in ("gpt-image-2", "gpt-image-2-4k"):
            with self.assertRaises(CommandError):
                generation_payload("cat", ImageOptions(model=model, quality="max"))

    def test_prompt_is_byte_for_byte_unchanged_without_explicit_transform(self):
        prompt = "  中文猫咪\nkeep this composition  "
        self.assertEqual(generation_payload(prompt, ImageOptions())["prompt"], prompt)
        self.assertEqual(job_payload(prompt, (), ImageOptions())["prompt"], prompt)

    def test_sync_size_is_validated_not_rounded(self):
        with self.assertRaisesRegex(CommandError, "job"):
            generation_payload("wallpaper", ImageOptions(size="1920x1080"))
        self.assertEqual(generation_payload("wallpaper", ImageOptions(size="1920x1088"))["size"], "1920x1088")

    def test_job_preserves_canvas_count_and_fit(self):
        payload = job_payload("wallpaper", (), ImageOptions(size="1920x1080", n=4, strict_size=True, upscale_fit="pad"))
        self.assertEqual(payload["size"], "1920x1080")
        self.assertEqual(payload["n"], 4)
        self.assertIs(payload["strict_size"], True)
        self.assertEqual(payload["upscale_fit"], "pad")

    def test_sync_rejects_ignored_job_parameters(self):
        for options in (ImageOptions(n=2), ImageOptions(upscale="4k"), ImageOptions(strict_size=True), ImageOptions(upscale_fit="cover")):
            with self.subTest(options=options), self.assertRaises(CommandError):
                generation_payload("cat", options)

    def test_job_rejects_ignored_sync_parameters(self):
        for options in (ImageOptions(response_format="url"), ImageOptions(moderation="low"), ImageOptions(output_format="jpeg", output_compression=80)):
            with self.subTest(options=options), self.assertRaises(CommandError):
                job_payload("cat", (), options)

    def test_size_and_parameter_boundaries(self):
        values = [ImageOptions(size="0x16"), ImageOptions(size="4096x4096"), ImageOptions(size="4096x16"),
                  ImageOptions(size="bad"), ImageOptions(n=0), ImageOptions(n=5), ImageOptions(background="transparent"),
                  ImageOptions(output_compression=101), ImageOptions(output_format="gif"), ImageOptions(quality="wrong")]
        for options in values:
            with self.subTest(options=options), self.assertRaises(CommandError):
                job_payload("cat", (), options)

    def test_edit_requires_valid_image_count(self):
        for images in ((), tuple("x" for _ in range(17))):
            with self.assertRaises(CommandError):
                edit_payload("cat", images, ImageOptions())

    def test_manifest_is_metadata_not_prompt_rewriting(self):
        manifest = [{"index": 0, "role": "identity", "filename": "face.png", "label": "reference"}]
        payload = edit_payload("change background", ("image",), ImageOptions(), manifest)
        self.assertEqual(payload["prompt"], "change background")
        self.assertEqual(payload["input_images_manifest"], manifest)

    def test_invalid_manifests_fail(self):
        for manifest in ([{"index": 1}], [{"index": 0}, {"index": 0}], [{"index": True}], [{"index": 0, "role": 1}]):
            with self.subTest(manifest=manifest), self.assertRaises(CommandError):
                edit_payload("cat", ("image",), ImageOptions(), manifest)

    def test_clean_background_is_explicit_and_preserves_request(self):
        prompt = "保留主体，只换背景"
        self.assertIn(prompt, clean_background_prompt(prompt))
        self.assertEqual(generation_payload(prompt, ImageOptions())["prompt"], prompt)

    def test_job_prompt_limit(self):
        with self.assertRaises(CommandError):
            job_payload("x" * 8001, (), ImageOptions())

    def test_non_integer_counts_and_compression(self):
        for options in (replace(ImageOptions(), n=True), replace(ImageOptions(), output_compression=True)):
            with self.assertRaises(CommandError):
                generation_payload("cat", options)

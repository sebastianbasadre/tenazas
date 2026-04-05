#!/usr/bin/env python3
"""
Tests for vision_pipeline.py — Image analysis with model chaining.

Covers:
- Image resolution (base64, file, telegram)
- Base64 encoding
- Vision model call (multimodal payload)
- Analysis model chain
- Full pipeline (extract → analyze)
- Format output
- Error handling (no image, model offline, bad file)
- Telegram download (mocked)

Corre: python3 tests/test_vision_pipeline.py
"""

import sys
import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import URLError

sys.path.insert(0, "/home/tenazas")
from runtime import vision_pipeline as vp


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_log = vp.LOG_FILE
        vp.LOG_FILE = self.tmpdir / "logs" / "vision_pipeline.log"

    def tearDown(self):
        vp.LOG_FILE = self._orig_log

    def _create_fake_image(self, name="test.jpg") -> Path:
        """Create a small fake image file."""
        p = self.tmpdir / name
        # Minimal JPEG-like bytes (not a real JPEG but enough for testing)
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        return p


# ── Image Resolution ────────────────────────────────────────────


class TestResolveImage(_TempDirMixin, unittest.TestCase):

    def test_base64_passthrough(self):
        b64 = base64.b64encode(b"fake image data").decode()
        result = vp.resolve_image(base64_data=b64)
        self.assertEqual(result, b64)

    def test_file_path(self):
        img = self._create_fake_image()
        result = vp.resolve_image(file_path=str(img))
        self.assertIsNotNone(result)
        # Should be valid base64
        decoded = base64.b64decode(result)
        self.assertTrue(len(decoded) > 0)

    def test_missing_file_returns_none(self):
        result = vp.resolve_image(file_path="/nonexistent/image.jpg")
        self.assertIsNone(result)

    def test_no_input_returns_none(self):
        self.assertIsNone(vp.resolve_image())

    def test_priority_base64_over_file(self):
        """base64_data takes priority over file_path."""
        img = self._create_fake_image()
        b64 = "direct_base64_data"
        result = vp.resolve_image(file_path=str(img), base64_data=b64)
        self.assertEqual(result, b64)

    @patch("runtime.vision_pipeline.download_telegram_photo")
    def test_telegram_fallback(self, mock_dl):
        mock_dl.return_value = b"telegram image bytes"
        result = vp.resolve_image(telegram_file_id="AgACAgIA_test")
        self.assertIsNotNone(result)
        mock_dl.assert_called_once_with("AgACAgIA_test")


class TestImageToBase64(unittest.TestCase):

    def test_encodes_correctly(self):
        data = b"hello world"
        result = vp.image_to_base64(data)
        self.assertEqual(base64.b64decode(result), data)

    def test_returns_string(self):
        self.assertIsInstance(vp.image_to_base64(b"test"), str)


class TestLoadImageFile(_TempDirMixin, unittest.TestCase):

    def test_loads_existing(self):
        img = self._create_fake_image()
        data = vp.load_image_file(str(img))
        self.assertIsNotNone(data)
        self.assertIsInstance(data, bytes)

    def test_missing_returns_none(self):
        self.assertIsNone(vp.load_image_file("/nope.jpg"))

    def test_too_large_returns_none(self):
        img = self.tmpdir / "huge.jpg"
        # Create file just over the limit
        orig_max = vp.MAX_IMAGE_SIZE
        vp.MAX_IMAGE_SIZE = 50  # tiny limit for test
        try:
            img.write_bytes(b"\x00" * 100)
            self.assertIsNone(vp.load_image_file(str(img)))
        finally:
            vp.MAX_IMAGE_SIZE = orig_max


# ── Telegram Download ───────────────────────────────────────────


class TestTelegramDownload(_TempDirMixin, unittest.TestCase):

    @patch("runtime.vision_pipeline._get_bot_token", return_value=None)
    def test_no_token_returns_none(self, mock_token):
        self.assertIsNone(vp.download_telegram_photo("file_id"))

    @patch("runtime.vision_pipeline.urlopen")
    @patch("runtime.vision_pipeline._get_bot_token", return_value="test_token")
    def test_successful_download(self, mock_token, mock_urlopen):
        # Mock getFile response
        get_file_resp = MagicMock()
        get_file_resp.read.return_value = json.dumps({
            "ok": True, "result": {"file_path": "photos/test.jpg"}
        }).encode()

        # Mock file download response
        download_resp = MagicMock()
        download_resp.read.return_value = b"\xff\xd8\xff\xe0fake_jpeg"

        mock_urlopen.side_effect = [get_file_resp, download_resp]

        result = vp.download_telegram_photo("AgACAgIA_test")
        self.assertIsNotNone(result)
        self.assertEqual(result, b"\xff\xd8\xff\xe0fake_jpeg")

    @patch("runtime.vision_pipeline.urlopen", side_effect=URLError("timeout"))
    @patch("runtime.vision_pipeline._get_bot_token", return_value="test_token")
    def test_network_error_returns_none(self, mock_token, mock_urlopen):
        self.assertIsNone(vp.download_telegram_photo("file_id"))


# ── Vision Model Call ───────────────────────────────────────────


class TestCallVisionModel(_TempDirMixin, unittest.TestCase):

    @patch("runtime.vision_pipeline.urlopen")
    def test_sends_images_field(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": "I see a cat"}).encode()
        mock_urlopen.return_value = mock_resp

        result = vp.call_vision_model("base64data", "describe")
        self.assertEqual(result, "I see a cat")

        # Verify the payload included images field
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data)
        self.assertIn("images", payload)
        self.assertEqual(payload["images"], ["base64data"])
        self.assertEqual(payload["model"], vp.VL_MODEL)

    @patch("runtime.vision_pipeline.urlopen", side_effect=URLError("offline"))
    def test_offline_returns_empty(self, mock_urlopen):
        self.assertEqual(vp.call_vision_model("b64", "prompt"), "")


class TestCallAnalysisModel(_TempDirMixin, unittest.TestCase):

    @patch("runtime.vision_pipeline.urlopen")
    def test_chains_extraction(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": "The image shows a receipt for $5000"}).encode()
        mock_urlopen.return_value = mock_resp

        result = vp.call_analysis_model("A photo of a receipt", "Analyze")
        self.assertIn("receipt", result)

        # Verify deepseek model is used
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        self.assertEqual(payload["model"], vp.ANALYSIS_MODEL)
        self.assertNotIn("images", payload)  # Analysis is text-only
        self.assertIn("receipt", payload["prompt"])

    @patch("runtime.vision_pipeline.urlopen", side_effect=URLError("offline"))
    def test_offline_returns_empty(self, mock_urlopen):
        self.assertEqual(vp.call_analysis_model("extraction", "prompt"), "")


# ── Full Pipeline ───────────────────────────────────────────────


class TestProcessImage(_TempDirMixin, unittest.TestCase):

    def test_no_image_returns_error(self):
        result = vp.process_image()
        self.assertEqual(result["status"], "error")
        self.assertIn("No image", result["error"])
        self.assertEqual(result["models_used"], [])

    @patch("runtime.vision_pipeline.call_analysis_model")
    @patch("runtime.vision_pipeline.call_vision_model")
    def test_full_chain(self, mock_vl, mock_analysis):
        mock_vl.return_value = "Photo shows a supermarket receipt totaling $18,500"
        mock_analysis.return_value = "This is a grocery expense of $18,500 at a supermarket"

        img = self._create_fake_image()
        result = vp.process_image(file_path=str(img), prompt="qué es esto")

        self.assertEqual(result["status"], "ok")
        self.assertIn("receipt", result["extraction"])
        self.assertIn("grocery", result["analysis"])
        self.assertEqual(len(result["models_used"]), 2)
        self.assertIn(vp.VL_MODEL, result["models_used"])
        self.assertIn(vp.ANALYSIS_MODEL, result["models_used"])

    @patch("runtime.vision_pipeline.call_vision_model")
    def test_extract_only(self, mock_vl):
        mock_vl.return_value = "A photo of a cat"

        img = self._create_fake_image()
        result = vp.process_image(file_path=str(img), analyze=False)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extraction"], "A photo of a cat")
        self.assertEqual(result["analysis"], "")
        self.assertEqual(result["models_used"], [vp.VL_MODEL])

    @patch("runtime.vision_pipeline.call_vision_model", return_value="")
    def test_vl_offline_returns_error(self, mock_vl):
        img = self._create_fake_image()
        result = vp.process_image(file_path=str(img))
        self.assertEqual(result["status"], "error")
        self.assertIn("no response", result["error"])

    @patch("runtime.vision_pipeline.call_analysis_model", return_value="")
    @patch("runtime.vision_pipeline.call_vision_model", return_value="extraction text")
    def test_analysis_offline_still_returns_extraction(self, mock_vl, mock_analysis):
        img = self._create_fake_image()
        result = vp.process_image(file_path=str(img))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extraction"], "extraction text")
        self.assertEqual(result["analysis"], "")
        # Only VL model counted since analysis returned empty
        self.assertEqual(result["models_used"], [vp.VL_MODEL])

    @patch("runtime.vision_pipeline.call_analysis_model")
    @patch("runtime.vision_pipeline.call_vision_model")
    def test_base64_input(self, mock_vl, mock_analysis):
        mock_vl.return_value = "description"
        mock_analysis.return_value = "analysis"

        b64 = base64.b64encode(b"fake").decode()
        result = vp.process_image(base64_data=b64)
        self.assertEqual(result["status"], "ok")

    @patch("runtime.vision_pipeline.call_analysis_model")
    @patch("runtime.vision_pipeline.call_vision_model")
    def test_custom_prompt_passed_to_vl(self, mock_vl, mock_analysis):
        mock_vl.return_value = "response"
        mock_analysis.return_value = "analysis"

        img = self._create_fake_image()
        vp.process_image(file_path=str(img), prompt="cuánto salió")

        # VL model should receive the custom prompt
        vl_call_prompt = mock_vl.call_args[0][1]
        self.assertEqual(vl_call_prompt, "cuánto salió")

    @patch("runtime.vision_pipeline.call_analysis_model")
    @patch("runtime.vision_pipeline.call_vision_model")
    def test_result_has_required_keys(self, mock_vl, mock_analysis):
        mock_vl.return_value = "test"
        mock_analysis.return_value = "test"

        img = self._create_fake_image()
        result = vp.process_image(file_path=str(img))
        for key in ("status", "extraction", "analysis", "models_used", "error"):
            self.assertIn(key, result, f"Missing key: {key}")


# ── Format ──────────────────────────────────────────────────────


class TestFormatResponse(unittest.TestCase):

    def test_format_error(self):
        result = {"status": "error", "error": "No image", "extraction": "", "analysis": ""}
        text = vp.format_response(result)
        self.assertIn("Error", text)

    def test_format_extraction_only(self):
        result = {"status": "ok", "extraction": "A cat", "analysis": "",
                  "error": None, "models_used": ["qwen2.5vl:7b"]}
        text = vp.format_response(result)
        self.assertIn("A cat", text)
        self.assertNotIn("---", text)

    def test_format_with_analysis(self):
        result = {"status": "ok", "extraction": "A receipt", "analysis": "Expense of $5000",
                  "error": None, "models_used": ["qwen2.5vl:7b", "deepseek-r1:14b"]}
        text = vp.format_response(result)
        self.assertIn("A receipt", text)
        self.assertIn("Expense", text)
        self.assertIn("---", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

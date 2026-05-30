import unittest

from mimo2api.audio_helpers import audio_media_type, extract_audio_payload, map_openai_tts_model, map_openai_tts_voice


class AudioHelpersTest(unittest.TestCase):
    def test_extract_audio_payload_from_chat_response(self):
        payload = {
            "choices": [
                {"message": {"audio": {"data": "YWJj", "format": "wav"}}}
            ]
        }

        audio_b64, audio_format = extract_audio_payload(payload)

        self.assertEqual(audio_b64, "YWJj")
        self.assertEqual(audio_format, "wav")

    def test_extract_audio_payload_recurses_nested_values(self):
        payload = {"outer": {"inner": {"audio": {"data": "ZGF0YQ==", "format": "mp3"}}}}

        audio_b64, audio_format = extract_audio_payload(payload)

        self.assertEqual(audio_b64, "ZGF0YQ==")
        self.assertEqual(audio_format, "mp3")

    def test_openai_tts_mapping_defaults(self):
        self.assertEqual(map_openai_tts_model("tts-1"), "mimo-v2-tts")
        self.assertEqual(map_openai_tts_voice("alloy"), "mimo_default")
        self.assertEqual(audio_media_type("wav"), "audio/wav")


if __name__ == "__main__":
    unittest.main()

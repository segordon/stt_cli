import unittest
from types import SimpleNamespace

from tests._module_loader import load_client_module


stt_client = load_client_module()


class ParseServerEndpointTests(unittest.TestCase):
    def test_empty_server_url_returns_none(self):
        self.assertIsNone(stt_client.parse_server_endpoint(""))

    def test_plain_host_defaults_to_tcp_and_default_port(self):
        self.assertEqual(stt_client.parse_server_endpoint("example.tailnet"), ("example.tailnet", 8765))

    def test_explicit_tcp_host_port(self):
        self.assertEqual(stt_client.parse_server_endpoint("tcp://100.64.1.2:9000"), ("100.64.1.2", 9000))

    def test_rejects_unsupported_scheme(self):
        with self.assertRaisesRegex(ValueError, "unsupported STT_SERVER scheme"):
            stt_client.parse_server_endpoint("http://example.com:8765")

    def test_rejects_url_with_path(self):
        with self.assertRaisesRegex(ValueError, "must not include a path"):
            stt_client.parse_server_endpoint("tcp://example.com:8765/path")

    def test_rejects_missing_host(self):
        with self.assertRaisesRegex(ValueError, "missing host"):
            stt_client.parse_server_endpoint("tcp://:8765")

    def test_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "invalid STT_SERVER port"):
            stt_client.parse_server_endpoint("tcp://example.com:70000")


class BuildTranscriptionOptionsTests(unittest.TestCase):
    def test_includes_only_overrides(self):
        args = SimpleNamespace(language=" en ", vad_filter=True, beam_size=5, best_of=7)
        self.assertEqual(
            stt_client.build_transcription_options(args),
            {"language": "en", "vad_filter": True, "beam_size": 5, "best_of": 7},
        )

    def test_omits_empty_optional_fields(self):
        args = SimpleNamespace(language="   ", vad_filter=None, beam_size=None, best_of=None)
        self.assertEqual(stt_client.build_transcription_options(args), {})


if __name__ == "__main__":
    unittest.main()

import io
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._module_loader import load_client_module


keystrel_client = load_client_module()


class ParseServerEndpointTests(unittest.TestCase):
    def test_empty_server_url_returns_none(self):
        self.assertIsNone(keystrel_client.parse_server_endpoint(""))

    def test_plain_host_defaults_to_tcp_and_default_port(self):
        self.assertEqual(keystrel_client.parse_server_endpoint("example.tailnet"), ("example.tailnet", 8765))

    def test_explicit_tcp_host_port(self):
        self.assertEqual(keystrel_client.parse_server_endpoint("tcp://100.64.1.2:9000"), ("100.64.1.2", 9000))

    def test_rejects_unsupported_scheme(self):
        with self.assertRaisesRegex(ValueError, "unsupported KEYSTREL_SERVER scheme"):
            keystrel_client.parse_server_endpoint("http://example.com:8765")

    def test_rejects_url_with_path(self):
        with self.assertRaisesRegex(ValueError, "must not include a path"):
            keystrel_client.parse_server_endpoint("tcp://example.com:8765/path")

    def test_rejects_missing_host(self):
        with self.assertRaisesRegex(ValueError, "missing host"):
            keystrel_client.parse_server_endpoint("tcp://:8765")

    def test_rejects_invalid_port(self):
        with self.assertRaisesRegex(ValueError, "invalid KEYSTREL_SERVER port"):
            keystrel_client.parse_server_endpoint("tcp://example.com:70000")


class BuildTranscriptionOptionsTests(unittest.TestCase):
    def test_includes_only_overrides(self):
        args = SimpleNamespace(language=" en ", vad_filter=True, beam_size=5, best_of=7)
        self.assertEqual(
            keystrel_client.build_transcription_options(args),
            {"language": "en", "vad_filter": True, "beam_size": 5, "best_of": 7},
        )

    def test_omits_empty_optional_fields(self):
        args = SimpleNamespace(language="   ", vad_filter=None, beam_size=None, best_of=None)
        self.assertEqual(keystrel_client.build_transcription_options(args), {})


class ParseArgsTests(unittest.TestCase):
    def test_cancel_file_env_is_normalized_and_expanded(self):
        with (
            mock.patch.dict(os.environ, {"KEYSTREL_CANCEL_FILE": "~/keystrel-cancel.flag"}, clear=False),
            mock.patch.object(sys, "argv", ["keystrel-client"]),
        ):
            args = keystrel_client.parse_args()

        self.assertEqual(args.cancel_file, str(Path("~/keystrel-cancel.flag").expanduser()))

    def test_parse_args_clamps_min_seconds_to_max_seconds(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(sys, "argv", ["keystrel-client", "--max-seconds", "1", "--min-seconds", "2"]),
        ):
            args = keystrel_client.parse_args()

        self.assertEqual(args.max_seconds, 1.0)
        self.assertEqual(args.min_seconds, 1.0)


class ClientParseHelpersTests(unittest.TestCase):
    def test_parse_bool_accepts_aliases_and_rejects_unknown(self):
        self.assertTrue(keystrel_client.parse_bool(" yes "))
        self.assertFalse(keystrel_client.parse_bool("OFF"))
        self.assertTrue(keystrel_client.parse_bool(True))

        with self.assertRaisesRegex(ValueError, "invalid boolean value"):
            keystrel_client.parse_bool("maybe")

    def test_parse_env_helpers_fallback_on_invalid_values(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "KEYSTREL_BAD_INT": "nope",
                    "KEYSTREL_BAD_FLOAT": "nan-nope",
                    "KEYSTREL_BAD_BOOL": "not-bool",
                    "KEYSTREL_BAD_CHOICE": "invalid",
                },
                clear=True,
            ),
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            self.assertEqual(keystrel_client.parse_env_int("KEYSTREL_BAD_INT", 7), 7)
            self.assertEqual(keystrel_client.parse_env_float("KEYSTREL_BAD_FLOAT", 1.5), 1.5)
            self.assertEqual(keystrel_client.parse_env_bool("KEYSTREL_BAD_BOOL", True), True)
            self.assertEqual(
                keystrel_client.parse_env_choice(
                    "KEYSTREL_BAD_CHOICE",
                    "pipewire",
                    {"auto", "pipewire", "paplay"},
                ),
                "pipewire",
            )

    def test_parse_env_choice_accepts_valid_value(self):
        with mock.patch.dict(os.environ, {"KEYSTREL_CHOICE": "PAPLAY"}, clear=True):
            result = keystrel_client.parse_env_choice("KEYSTREL_CHOICE", "auto", {"auto", "paplay"})
        self.assertEqual(result, "paplay")

    def test_normalize_audio_device_variants(self):
        self.assertIsNone(keystrel_client.normalize_audio_device(None))
        self.assertIsNone(keystrel_client.normalize_audio_device("  "))
        self.assertEqual(keystrel_client.normalize_audio_device("42"), 42)
        self.assertEqual(keystrel_client.normalize_audio_device("UM10"), "UM10")
        self.assertEqual(keystrel_client.normalize_audio_device(7), 7)


if __name__ == "__main__":
    unittest.main()

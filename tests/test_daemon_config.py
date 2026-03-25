import io
import os
import sys
import unittest
from unittest import mock

from tests._module_loader import load_daemon_module


keystrel_daemon = load_daemon_module()


class DaemonParseHelpersTests(unittest.TestCase):
    def test_parse_bool_accepts_aliases_and_rejects_unknown(self):
        self.assertTrue(keystrel_daemon.parse_bool("ON"))
        self.assertFalse(keystrel_daemon.parse_bool("0"))
        self.assertTrue(keystrel_daemon.parse_bool(True))

        with self.assertRaisesRegex(ValueError, "invalid boolean value"):
            keystrel_daemon.parse_bool("maybe")

    def test_parse_env_helpers_fallback_on_invalid_values(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    "KEYSTREL_BAD_INT": "oops",
                    "KEYSTREL_BAD_BOOL": "not-a-bool",
                },
                clear=True,
            ),
            mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(keystrel_daemon.parse_env_int("KEYSTREL_BAD_INT", 9), 9)
            self.assertEqual(keystrel_daemon.parse_env_bool("KEYSTREL_BAD_BOOL", False), False)

        output = stderr.getvalue()
        self.assertIn("invalid KEYSTREL_BAD_INT", output)
        self.assertIn("invalid KEYSTREL_BAD_BOOL", output)


class DaemonParseArgsTests(unittest.TestCase):
    def test_parse_args_reads_env_and_types(self):
        env = {
            "KEYSTREL_SOCKET": "~/custom.sock",
            "KEYSTREL_TCP_LISTEN": "100.64.0.1",
            "KEYSTREL_TCP_PORT": "9001",
            "KEYSTREL_SERVER_TOKEN": "abc",
            "KEYSTREL_MODEL": "tiny",
            "KEYSTREL_DEVICE": "cpu",
            "KEYSTREL_COMPUTE_TYPE": "int8",
            "KEYSTREL_BEAM_SIZE": "3",
            "KEYSTREL_BEST_OF": "4",
            "KEYSTREL_VAD_FILTER": "0",
            "KEYSTREL_LANGUAGE": "en",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(sys, "argv", ["keystrel-daemon"]),
        ):
            args = keystrel_daemon.parse_args()

        self.assertEqual(args.socket, "~/custom.sock")
        self.assertEqual(args.tcp_listen, "100.64.0.1")
        self.assertEqual(args.tcp_port, 9001)
        self.assertEqual(args.server_token, "abc")
        self.assertEqual(args.model, "tiny")
        self.assertEqual(args.device, "cpu")
        self.assertEqual(args.compute_type, "int8")
        self.assertEqual(args.beam_size, 3)
        self.assertEqual(args.best_of, 4)
        self.assertFalse(args.vad_filter)
        self.assertEqual(args.language, "en")


if __name__ == "__main__":
    unittest.main()

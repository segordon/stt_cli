import io
import os
import unittest
from unittest import mock

from tests._module_loader import load_client_module, load_daemon_module


keystrel_client = load_client_module()
keystrel_daemon = load_daemon_module()


class LegacyEnvCompatibilityTests(unittest.TestCase):
    def setUp(self):
        keystrel_client._LEGACY_ENV_WARNED.clear()
        keystrel_daemon._LEGACY_ENV_WARNED.clear()

    def test_client_legacy_env_warns_once(self):
        with mock.patch.dict(os.environ, {"STT_SERVER": "tcp://legacy:8765"}, clear=True):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                value1 = keystrel_client.get_env("KEYSTREL_SERVER", "")
                value2 = keystrel_client.get_env("KEYSTREL_SERVER", "")

        self.assertEqual(value1, "tcp://legacy:8765")
        self.assertEqual(value2, "tcp://legacy:8765")
        self.assertEqual(stderr.getvalue().count("STT_SERVER is deprecated"), 1)

    def test_client_prefers_keystrel_env_without_warning(self):
        with mock.patch.dict(
            os.environ,
            {
                "KEYSTREL_SERVER": "tcp://new:8765",
                "STT_SERVER": "tcp://legacy:8765",
            },
            clear=True,
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                value = keystrel_client.get_env("KEYSTREL_SERVER", "")

        self.assertEqual(value, "tcp://new:8765")
        self.assertEqual(stderr.getvalue(), "")

    def test_daemon_legacy_env_warns_once(self):
        with mock.patch.dict(os.environ, {"STT_MODEL": "tiny"}, clear=True):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                value1 = keystrel_daemon.get_env("KEYSTREL_MODEL", "")
                value2 = keystrel_daemon.get_env("KEYSTREL_MODEL", "")

        self.assertEqual(value1, "tiny")
        self.assertEqual(value2, "tiny")
        self.assertEqual(stderr.getvalue().count("STT_MODEL is deprecated"), 1)


if __name__ == "__main__":
    unittest.main()

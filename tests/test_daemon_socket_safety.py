import socket
import tempfile
import unittest
from pathlib import Path

from tests._module_loader import load_daemon_module


stt_daemon = load_daemon_module()


class DaemonSocketSafetyTests(unittest.TestCase):
    def test_remove_existing_socket_ignores_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_path = Path(tmp_dir) / "missing.sock"
            stt_daemon.remove_existing_socket(missing_path)
            self.assertFalse(missing_path.exists())

    def test_remove_existing_socket_refuses_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "not-a-socket"
            file_path.write_text("data", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "refusing to remove non-socket path"):
                stt_daemon.remove_existing_socket(file_path)
            self.assertTrue(file_path.exists())

    def test_remove_existing_socket_unlinks_socket_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            socket_path = Path(tmp_dir) / "test.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(str(socket_path))
                sock.listen(1)
                self.assertTrue(socket_path.exists())
                stt_daemon.remove_existing_socket(socket_path)
                self.assertFalse(socket_path.exists())
            finally:
                sock.close()


if __name__ == "__main__":
    unittest.main()

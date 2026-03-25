import base64
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._module_loader import load_daemon_module


keystrel_daemon = load_daemon_module()


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, **options):
        self.calls.append((audio_path, dict(options)))
        return [SimpleNamespace(text="hello from unix fake model")], SimpleNamespace(
            language="en",
            language_probability=0.98,
        )


class RunningUnixServer:
    def __init__(self, max_request_bytes=4096, max_audio_bytes=1024, model=None):
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        self.socket_path = self.temp_dir / "keystrel.sock"
        self.model = model if model is not None else FakeModel()
        self.server = keystrel_daemon.KeystrelUnixServer(
            self.socket_path,
            self.model,
            {
                "beam_size": 1,
                "best_of": 1,
                "vad_filter": True,
                "condition_on_previous_text": False,
            },
            max_request_bytes=max_request_bytes,
            max_audio_bytes=max_audio_bytes,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.socket_path.unlink(missing_ok=True)
        self.temp_dir_obj.cleanup()

    def request(self, payload):
        wire = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
        return self._request_raw(wire)

    def request_raw_line(self, raw_line):
        if isinstance(raw_line, str):
            raw_line = raw_line.encode("utf-8")
        return self._request_raw(raw_line)

    def _request_raw(self, wire):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(str(self.socket_path))
            sock.sendall(wire)
            response = b""
            while not response.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        return json.loads(response.decode("utf-8"))


class DaemonUnixTransportTests(unittest.TestCase):
    def test_accepts_audio_path_without_auth(self):
        with RunningUnixServer() as server:
            audio_path = server.temp_dir / "sample.wav"
            audio_path.write_bytes(b"fake-local-wav")

            response = server.request(
                {
                    "audio_path": str(audio_path),
                    "language": "en",
                    "beam_size": 3,
                }
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["text"], "hello from unix fake model")
            self.assertEqual(response["language"], "en")
            self.assertEqual(len(server.model.calls), 1)

            called_audio_path, options = server.model.calls[0]
            self.assertEqual(Path(called_audio_path), audio_path)
            self.assertEqual(options["language"], "en")
            self.assertEqual(options["beam_size"], 3)
            self.assertEqual(options["best_of"], 1)

    def test_ignores_auth_field_for_unix_transport(self):
        with RunningUnixServer() as server:
            audio_path = server.temp_dir / "sample.wav"
            audio_path.write_bytes(b"fake-local-wav")
            response = server.request(
                {
                    "audio_path": str(audio_path),
                    "auth_token": "not-required-on-unix",
                }
            )
            self.assertTrue(response["ok"])
            self.assertEqual(len(server.model.calls), 1)

    def test_rejects_missing_audio_payload(self):
        with RunningUnixServer() as server:
            response = server.request({})
            self.assertFalse(response["ok"])
            self.assertIn("missing required audio payload", response["error"])

    def test_rejects_nonexistent_audio_path(self):
        with RunningUnixServer() as server:
            response = server.request({"audio_path": str(server.temp_dir / "missing.wav")})
            self.assertFalse(response["ok"])
            self.assertIn("audio file does not exist", response["error"])

    def test_rejects_invalid_json(self):
        with RunningUnixServer() as server:
            response = server.request_raw_line("{not-json}\n")
            self.assertFalse(response["ok"])
            self.assertIn("invalid JSON", response["error"])

    def test_rejects_oversized_request_line(self):
        with RunningUnixServer(max_request_bytes=80) as server:
            oversized = '{"audio_path":"%s"}\n' % ("a" * 300)
            response = server.request_raw_line(oversized)
            self.assertFalse(response["ok"])
            self.assertIn("request too large", response["error"])

    def test_accepts_audio_b64_and_cleans_temp_file(self):
        with RunningUnixServer() as server:
            response = server.request({"audio_b64": base64.b64encode(b"abc").decode("ascii")})
            self.assertTrue(response["ok"])
            self.assertEqual(len(server.model.calls), 1)

            called_audio_path, _ = server.model.calls[0]
            self.assertFalse(Path(called_audio_path).exists())

    def test_rejects_invalid_audio_b64_payload(self):
        with RunningUnixServer() as server:
            response = server.request({"audio_b64": "%%%"})
            self.assertFalse(response["ok"])
            self.assertIn("invalid audio_b64 payload", response["error"])

    def test_rejects_oversized_audio_payload(self):
        with RunningUnixServer(max_audio_bytes=3) as server:
            response = server.request({"audio_b64": base64.b64encode(b"abcd").decode("ascii")})
            self.assertFalse(response["ok"])
            self.assertIn("audio payload exceeds size limit", response["error"])

    def test_rejects_empty_audio_b64_payload(self):
        with RunningUnixServer() as server:
            with mock.patch.object(keystrel_daemon.base64, "b64decode", return_value=b""):
                response = server.request({"audio_b64": "AAAA"})

        self.assertFalse(response["ok"])
        self.assertIn("audio_b64 payload is empty", response["error"])

    def test_transcription_failure_returns_error_and_cleans_temp_audio(self):
        class _FailingModel:
            def __init__(self):
                self.paths = []

            def transcribe(self, audio_path, **options):  # noqa: ARG002
                self.paths.append(audio_path)
                raise RuntimeError("transcribe boom")

        model = _FailingModel()
        with RunningUnixServer(model=model) as server:
            response = server.request({"audio_b64": base64.b64encode(b"abc").decode("ascii")})

        self.assertFalse(response["ok"])
        self.assertIn("transcription failed", response["error"])
        self.assertEqual(len(model.paths), 1)
        self.assertFalse(Path(model.paths[0]).exists())

    def test_temp_cleanup_ignores_unlink_oserror(self):
        with RunningUnixServer() as server:
            with mock.patch.object(keystrel_daemon.Path, "unlink", side_effect=OSError("deny")):
                response = server.request({"audio_b64": base64.b64encode(b"abc").decode("ascii")})

        self.assertTrue(response["ok"])


if __name__ == "__main__":
    unittest.main()

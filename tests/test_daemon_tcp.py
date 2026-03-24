import base64
import json
import socket
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests._module_loader import load_daemon_module


keystrel_daemon = load_daemon_module()


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, **options):
        self.calls.append((audio_path, dict(options)))
        return [SimpleNamespace(text="hello from fake model")], SimpleNamespace(
            language="en",
            language_probability=0.99,
        )


class RunningTCPServer:
    def __init__(self, max_request_bytes=4096, max_audio_bytes=1024, token="secret-token"):
        self.model = FakeModel()
        self.server = keystrel_daemon.KeystrelTCPServer(
            "127.0.0.1",
            0,
            self.model,
            {
                "beam_size": 1,
                "best_of": 1,
                "vad_filter": True,
                "condition_on_previous_text": False,
            },
            max_request_bytes=max_request_bytes,
            max_audio_bytes=max_audio_bytes,
            auth_token=token,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    @property
    def address(self):
        host, port = self.server.server_address
        return host, port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def request(self, payload):
        wire = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
        return self._request_raw(wire)

    def request_raw_line(self, raw_line):
        if isinstance(raw_line, str):
            raw_line = raw_line.encode("utf-8")
        return self._request_raw(raw_line)

    def _request_raw(self, wire):
        with socket.create_connection(self.address, timeout=2.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(wire)
            response = b""
            while not response.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        return json.loads(response.decode("utf-8"))


class DaemonTCPTransportTests(unittest.TestCase):
    def test_request_must_be_json_object(self):
        with RunningTCPServer() as server:
            response = server.request_raw_line("[]\n")
            self.assertFalse(response["ok"])
            self.assertIn("request must be a JSON object", response["error"])

    def test_requires_auth_token(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_b64": base64.b64encode(b"abc").decode("ascii"),
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("missing auth token", response["error"])

    def test_rejects_invalid_auth_token(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_b64": base64.b64encode(b"abc").decode("ascii"),
                    "auth_token": "wrong-token",
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("invalid auth token", response["error"])

    def test_rejects_audio_path_on_tcp(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_path": "/tmp/not-allowed.wav",
                    "auth_token": "secret-token",
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("audio_path is not allowed", response["error"])

    def test_rejects_oversized_audio_payload(self):
        with RunningTCPServer(max_audio_bytes=4) as server:
            response = server.request(
                {
                    "audio_b64": base64.b64encode(b"12345").decode("ascii"),
                    "auth_token": "secret-token",
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("audio payload exceeds size limit", response["error"])

    def test_rejects_invalid_audio_b64_payload(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_b64": "%%%",
                    "auth_token": "secret-token",
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("invalid audio_b64 payload", response["error"])

    def test_rejects_invalid_vad_filter_override(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_b64": base64.b64encode(b"abc").decode("ascii"),
                    "auth_token": "secret-token",
                    "vad_filter": "definitely-not-a-bool",
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("invalid boolean value", response["error"])

    def test_rejects_invalid_beam_size_override(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_b64": base64.b64encode(b"abc").decode("ascii"),
                    "auth_token": "secret-token",
                    "beam_size": "not-an-int",
                }
            )
            self.assertFalse(response["ok"])
            self.assertIn("invalid integer for beam_size", response["error"])

    def test_rejects_oversized_request_line(self):
        with RunningTCPServer(max_request_bytes=80) as server:
            oversized = '{"auth_token":"secret-token","audio_b64":"%s"}\n' % ("a" * 200)
            response = server.request_raw_line(oversized)
            self.assertFalse(response["ok"])
            self.assertIn("request too large", response["error"])

    def test_accepts_valid_request_and_applies_overrides(self):
        with RunningTCPServer() as server:
            response = server.request(
                {
                    "audio_b64": base64.b64encode(b"tiny-wav-placeholder").decode("ascii"),
                    "auth_token": "secret-token",
                    "language": "en",
                    "vad_filter": False,
                    "beam_size": 3,
                    "best_of": 5,
                }
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["text"], "hello from fake model")
            self.assertEqual(response["language"], "en")
            self.assertEqual(len(server.model.calls), 1)

            audio_path, options = server.model.calls[0]
            self.assertFalse(Path(audio_path).exists())
            self.assertEqual(options["language"], "en")
            self.assertEqual(options["vad_filter"], False)
            self.assertEqual(options["beam_size"], 3)
            self.assertEqual(options["best_of"], 5)

    def test_soak_repeated_requests_leave_no_temp_files(self):
        request_count = 25
        with RunningTCPServer() as server:
            for idx in range(request_count):
                response = server.request(
                    {
                        "audio_b64": base64.b64encode(f"payload-{idx}".encode("utf-8")).decode("ascii"),
                        "auth_token": "secret-token",
                    }
                )
                self.assertTrue(response["ok"])

            self.assertEqual(len(server.model.calls), request_count)
            for audio_path, _options in server.model.calls:
                self.assertFalse(Path(audio_path).exists())


if __name__ == "__main__":
    unittest.main()

import json
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests._module_loader import load_client_module


keystrel_client = load_client_module()


class _TCPResponderHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = b""
        while not request.endswith(b"\n"):
            chunk = self.request.recv(4096)
            if not chunk:
                break
            request += chunk
        self.server.received.append(request)
        self.server.behavior(self.request, request)


class _TCPResponderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RunningTCPResponder:
    def __init__(self, behavior):
        self.server = _TCPResponderServer(("127.0.0.1", 0), _TCPResponderHandler)
        self.server.behavior = behavior
        self.server.received = []
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    @property
    def host_port(self):
        host, port = self.server.server_address
        return host, port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


class _UnixResponderHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = self.rfile.readline()
        self.server.received.append(request)
        self.server.behavior(self.request, request)


class _UnixResponderServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class RunningUnixResponder:
    def __init__(self, behavior):
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        self.socket_path = self.temp_dir / "response.sock"
        self.server = _UnixResponderServer(str(self.socket_path), _UnixResponderHandler)
        self.server.behavior = behavior
        self.server.received = []
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if self.socket_path.exists():
                break
            time.sleep(0.01)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.socket_path.unlink(missing_ok=True)
        self.temp_dir_obj.cleanup()


class ClientTransportTests(unittest.TestCase):
    def test_send_tcp_request_success(self):
        with RunningTCPResponder(lambda conn, req: conn.sendall(b'{"ok":true,"text":"hello"}\n')) as server:
            host, port = server.host_port
            response = keystrel_client.send_tcp_request(host, port, {"hello": "world"}, timeout_s=1.0)
            self.assertTrue(response["ok"])
            self.assertEqual(response["text"], "hello")

            sent_payload = json.loads(server.server.received[0].decode("utf-8"))
            self.assertEqual(sent_payload["hello"], "world")

    def test_send_tcp_request_invalid_json_response(self):
        with RunningTCPResponder(lambda conn, req: conn.sendall(b"not-json\n")) as server:
            host, port = server.host_port
            with self.assertRaisesRegex(RuntimeError, "invalid JSON response from remote server"):
                keystrel_client.send_tcp_request(host, port, {"x": 1}, timeout_s=1.0)

    def test_send_tcp_request_empty_response(self):
        with RunningTCPResponder(lambda conn, req: None) as server:
            host, port = server.host_port
            with self.assertRaisesRegex(RuntimeError, "empty response from remote server"):
                keystrel_client.send_tcp_request(host, port, {"x": 1}, timeout_s=1.0)

    def test_send_tcp_request_response_size_limit(self):
        huge_response = b'{"ok":true,"blob":"' + (b"a" * 500) + b'"}\n'
        with RunningTCPResponder(lambda conn, req: conn.sendall(huge_response)) as server:
            host, port = server.host_port
            with self.assertRaisesRegex(RuntimeError, "remote response exceeded size limit"):
                keystrel_client.send_tcp_request(
                    host,
                    port,
                    {"x": 1},
                    timeout_s=1.0,
                    max_response_bytes=64,
                )

    def test_send_tcp_request_read_timeout(self):
        def _slow(conn, req):
            time.sleep(0.2)

        with RunningTCPResponder(_slow) as server:
            host, port = server.host_port
            with self.assertRaisesRegex(TimeoutError, "remote server request timed out"):
                keystrel_client.send_tcp_request(host, port, {"x": 1}, timeout_s=0.05)

    def test_send_tcp_request_connect_failure(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            host, port = probe.getsockname()

        with self.assertRaisesRegex(RuntimeError, "remote server connection failed"):
            keystrel_client.send_tcp_request(host, port, {"x": 1}, timeout_s=0.2)

    def test_send_tcp_request_connect_timeout(self):
        with mock.patch.object(keystrel_client.socket, "create_connection", side_effect=socket.timeout("slow")):
            with self.assertRaisesRegex(TimeoutError, "remote server connect timed out"):
                keystrel_client.send_tcp_request("127.0.0.1", 8765, {"x": 1}, timeout_s=0.2)

    def test_send_unix_request_success(self):
        with RunningUnixResponder(lambda conn, req: conn.sendall(b'{"ok":true,"text":"unix"}\n')) as server:
            response = keystrel_client.send_unix_request(server.socket_path, {"hello": "unix"}, timeout_s=1.0)
            self.assertTrue(response["ok"])
            self.assertEqual(response["text"], "unix")

            sent_payload = json.loads(server.server.received[0].decode("utf-8"))
            self.assertEqual(sent_payload["hello"], "unix")

    def test_send_unix_request_empty_response(self):
        with RunningUnixResponder(lambda conn, req: None) as server:
            with self.assertRaisesRegex(RuntimeError, "empty response from daemon"):
                keystrel_client.send_unix_request(server.socket_path, {"x": 1}, timeout_s=1.0)

    def test_send_unix_request_timeout(self):
        def _slow(conn, req):
            time.sleep(0.2)

        with RunningUnixResponder(_slow) as server:
            with self.assertRaisesRegex(TimeoutError, "daemon request timed out"):
                keystrel_client.send_unix_request(server.socket_path, {"x": 1}, timeout_s=0.05)

    def test_send_unix_request_invalid_json_response(self):
        with RunningUnixResponder(lambda conn, req: conn.sendall(b"not-json\n")) as server:
            with self.assertRaises(json.JSONDecodeError):
                keystrel_client.send_unix_request(server.socket_path, {"x": 1}, timeout_s=1.0)


if __name__ == "__main__":
    unittest.main()

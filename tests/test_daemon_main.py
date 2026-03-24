import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._module_loader import load_daemon_module


keystrel_daemon = load_daemon_module()


def _base_args(**overrides):
    values = {
        "socket": "",
        "tcp_listen": "",
        "tcp_port": 8765,
        "server_token": "",
        "max_request_bytes": 1024,
        "max_audio_bytes": 1024,
        "model": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 1,
        "best_of": 1,
        "vad_filter": True,
        "language": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeWhisperModel:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeServer:
    def __init__(self):
        self.transport = "unknown"
        self.max_request_bytes = 0
        self.shutdown_called = False
        self.server_close_called = False
        self.stop_event = threading.Event()

    def serve_forever(self, poll_interval=0.2):  # noqa: ARG002
        self.stop_event.wait(timeout=2.0)

    def shutdown(self):
        self.shutdown_called = True
        self.stop_event.set()

    def server_close(self):
        self.server_close_called = True


class _FakeUnixServer(_FakeServer):
    instances = []

    def __init__(
        self,
        socket_path,
        model,
        default_options,
        max_request_bytes,
        max_audio_bytes,
    ):
        super().__init__()
        self.transport = "unix"
        self.socket_path = Path(socket_path)
        self.model = model
        self.default_options = default_options
        self.max_request_bytes = max_request_bytes
        self.max_audio_bytes = max_audio_bytes
        _FakeUnixServer.instances.append(self)


class _FakeTCPServer(_FakeServer):
    instances = []

    def __init__(
        self,
        listen_host,
        listen_port,
        model,
        default_options,
        max_request_bytes,
        max_audio_bytes,
        auth_token,
    ):
        super().__init__()
        self.transport = "tcp"
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.model = model
        self.default_options = default_options
        self.max_request_bytes = max_request_bytes
        self.max_audio_bytes = max_audio_bytes
        self.auth_token = auth_token
        _FakeTCPServer.instances.append(self)


class DaemonMainGuardTests(unittest.TestCase):
    def test_tcp_listener_requires_token(self):
        args = _base_args(socket="", tcp_listen="127.0.0.1", server_token="")
        with mock.patch.object(keystrel_daemon, "parse_args", return_value=args):
            with self.assertRaises(SystemExit) as ctx:
                keystrel_daemon.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_tcp_port_exits(self):
        args = _base_args(socket="", tcp_listen="127.0.0.1", server_token="token", tcp_port=70000)
        with mock.patch.object(keystrel_daemon, "parse_args", return_value=args):
            with self.assertRaises(SystemExit) as ctx:
                keystrel_daemon.main()
        self.assertEqual(ctx.exception.code, 2)

    def test_no_transports_enabled_exits(self):
        args = _base_args(socket="", tcp_listen="", server_token="")
        with (
            mock.patch.object(keystrel_daemon, "parse_args", return_value=args),
            mock.patch.object(keystrel_daemon, "WhisperModel", _FakeWhisperModel),
        ):
            with self.assertRaises(SystemExit) as ctx:
                keystrel_daemon.main()
        self.assertEqual(ctx.exception.code, 2)


class DaemonMainDualTransportTests(unittest.TestCase):
    def test_dual_transport_startup_and_shutdown_path(self):
        _FakeUnixServer.instances = []
        _FakeTCPServer.instances = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            socket_path = Path(tmp_dir) / "keystrel.sock"
            args = _base_args(
                socket=str(socket_path),
                tcp_listen="127.0.0.1",
                server_token="token",
                tcp_port=8765,
            )

            registered_handlers = {}

            def _fake_signal(sig, handler):
                registered_handlers[sig] = handler
                return handler

            def _trigger_shutdown():
                deadline = time.time() + 1.0
                while signal.SIGTERM not in registered_handlers and time.time() < deadline:
                    time.sleep(0.01)
                handler = registered_handlers.get(signal.SIGTERM)
                if handler is not None:
                    handler(signal.SIGTERM, None)

            trigger_thread = threading.Thread(target=_trigger_shutdown, daemon=True)
            trigger_thread.start()

            with (
                mock.patch.object(keystrel_daemon, "parse_args", return_value=args),
                mock.patch.object(keystrel_daemon, "WhisperModel", _FakeWhisperModel),
                mock.patch.object(keystrel_daemon, "KeystrelUnixServer", _FakeUnixServer),
                mock.patch.object(keystrel_daemon, "KeystrelTCPServer", _FakeTCPServer),
                mock.patch.object(keystrel_daemon.signal, "signal", side_effect=_fake_signal),
            ):
                keystrel_daemon.main()

            trigger_thread.join(timeout=1.0)

        self.assertEqual(len(_FakeUnixServer.instances), 1)
        self.assertEqual(len(_FakeTCPServer.instances), 1)

        unix_server = _FakeUnixServer.instances[0]
        tcp_server = _FakeTCPServer.instances[0]

        self.assertTrue(unix_server.shutdown_called)
        self.assertTrue(unix_server.server_close_called)
        self.assertTrue(tcp_server.shutdown_called)
        self.assertTrue(tcp_server.server_close_called)


if __name__ == "__main__":
    unittest.main()

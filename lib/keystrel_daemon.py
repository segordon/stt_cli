#!/usr/bin/env python3

import argparse
import base64
import binascii
import hmac
import json
import os
import signal
import socketserver
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import cast

from keystrel_env import env_candidates as shared_env_candidates
from keystrel_env import get_env as shared_get_env
from keystrel_env import parse_bool as shared_parse_bool
from keystrel_env import parse_env_bool as shared_parse_env_bool
from keystrel_env import parse_env_int as shared_parse_env_int

from faster_whisper import WhisperModel  # type: ignore[import-not-found]


def parse_bool(value):
    return shared_parse_bool(value)


def _env_candidates(name):
    return shared_env_candidates(name)


_LEGACY_ENV_WARNED = set()


def get_env(name, default=None):
    return shared_get_env(name, default, _LEGACY_ENV_WARNED, "keystrel-daemon")


def parse_env_int(name, default):
    return shared_parse_env_int(name, default, get_env, "keystrel-daemon")


def parse_env_bool(name, default):
    return shared_parse_env_bool(name, default, get_env, "keystrel-daemon")


def remove_existing_socket(path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return

    if stat.S_ISSOCK(st.st_mode):
        path.unlink()
        return

    raise RuntimeError(f"refusing to remove non-socket path: {path}")


class KeystrelUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(
        self,
        socket_path,
        model,
        default_options,
        max_request_bytes,
        max_audio_bytes,
    ):
        super().__init__(str(socket_path), KeystrelHandler)
        self.model = model
        self.default_options = default_options
        self.max_request_bytes = max_request_bytes
        self.max_audio_bytes = max_audio_bytes
        self.transport = "unix"
        self.require_token = False
        self.auth_token = ""
        self.socket_path = Path(socket_path)


class KeystrelTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

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
        super().__init__((listen_host, listen_port), KeystrelHandler)
        self.model = model
        self.default_options = default_options
        self.max_request_bytes = max_request_bytes
        self.max_audio_bytes = max_audio_bytes
        self.transport = "tcp"
        self.require_token = True
        self.auth_token = auth_token
        self.listen_host = listen_host
        self.listen_port = listen_port


class KeystrelHandler(socketserver.StreamRequestHandler):
    def send_json(self, payload):
        self.wfile.write((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))

    def _read_request_line(self, max_request_bytes):
        line = self.rfile.readline(max_request_bytes + 1)
        if not line:
            return None

        if len(line) > max_request_bytes:
            self.send_json({"ok": False, "error": "request too large"})
            return None

        return line

    def _check_auth(self, server, request):
        if not server.require_token:
            return True

        token = request.get("auth_token")
        if not isinstance(token, str) or not token:
            self.send_json({"ok": False, "error": "unauthorized: missing auth token"})
            return False

        if not hmac.compare_digest(token, server.auth_token):
            self.send_json({"ok": False, "error": "unauthorized: invalid auth token"})
            return False

        return True

    def _resolve_audio_path(self, server, request):
        audio_b64 = request.get("audio_b64")
        audio_path = request.get("audio_path")

        if isinstance(audio_b64, str) and audio_b64:
            try:
                audio_bytes = base64.b64decode(audio_b64.encode("ascii"), validate=True)
            except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
                self.send_json({"ok": False, "error": f"invalid audio_b64 payload: {exc}"})
                return None, None

            if not audio_bytes:
                self.send_json({"ok": False, "error": "audio_b64 payload is empty"})
                return None, None

            if len(audio_bytes) > server.max_audio_bytes:
                self.send_json({"ok": False, "error": "audio payload exceeds size limit"})
                return None, None

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                temp_audio_path = Path(tmp.name)

            return temp_audio_path, temp_audio_path

        if isinstance(audio_path, str) and audio_path:
            if server.transport == "tcp":
                self.send_json({"ok": False, "error": "audio_path is not allowed over tcp transport"})
                return None, None

            local_audio_path = Path(audio_path).expanduser()
            if not local_audio_path.is_file():
                self.send_json({"ok": False, "error": f"audio file does not exist: {local_audio_path}"})
                return None, None

            return local_audio_path, None

        self.send_json({"ok": False, "error": "missing required audio payload (audio_path or audio_b64)"})
        return None, None

    def _build_options(self, server, request):
        options = dict(server.default_options)

        for key in ("language", "task"):
            if key in request and isinstance(request[key], str) and request[key].strip():
                options[key] = request[key].strip()

        if "vad_filter" in request:
            try:
                options["vad_filter"] = parse_bool(request["vad_filter"])
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)})
                return None

        for key in ("beam_size", "best_of"):
            if key in request:
                try:
                    options[key] = int(request[key])
                except (TypeError, ValueError):
                    self.send_json({"ok": False, "error": f"invalid integer for {key}"})
                    return None

        return options

    def _parse_request_payload(self, line):
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": f"invalid JSON: {exc}"})
            return None

        if not isinstance(request, dict):
            self.send_json({"ok": False, "error": "request must be a JSON object"})
            return None

        return request

    def _transcribe_request(self, server, audio_path, options):
        started_at = time.perf_counter()
        try:
            segments, info = server.model.transcribe(str(audio_path), **options)
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001
            self.send_json({"ok": False, "error": f"transcription failed: {exc}"})
            return None

        elapsed_s = time.perf_counter() - started_at
        return {
            "ok": True,
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "elapsed_s": round(elapsed_s, 3),
        }

    def _cleanup_temp_audio(self, temp_audio_path):
        if temp_audio_path is None:
            return
        try:
            temp_audio_path.unlink(missing_ok=True)
        except OSError:
            pass

    def handle(self):
        server = cast(KeystrelUnixServer | KeystrelTCPServer, self.server)

        line = self._read_request_line(server.max_request_bytes)
        if line is None:
            return

        request = self._parse_request_payload(line)
        if request is None:
            return

        if not self._check_auth(server, request):
            return

        audio_path, temp_audio_path = self._resolve_audio_path(server, request)
        if audio_path is None:
            return

        try:
            options = self._build_options(server, request)
            if options is None:
                return

            response = self._transcribe_request(server, audio_path, options)
            if response is None:
                return

            self.send_json(response)
        finally:
            self._cleanup_temp_audio(temp_audio_path)


def _add_transport_args(parser):
    parser.add_argument(
        "--socket",
        default=get_env("KEYSTREL_SOCKET", "~/.cache/keystrel/faster-whisper.sock"),
        help="Unix socket path (set empty to disable)",
    )
    parser.add_argument(
        "--tcp-listen",
        default=get_env("KEYSTREL_TCP_LISTEN", ""),
        help="optional TCP listen address (recommended: Tailscale IP)",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=parse_env_int("KEYSTREL_TCP_PORT", 8765),
        help="TCP listen port",
    )
    parser.add_argument(
        "--server-token",
        default=get_env("KEYSTREL_SERVER_TOKEN", ""),
        help="shared token required for TCP requests",
    )


def _add_limit_args(parser):
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=parse_env_int("KEYSTREL_MAX_REQUEST_BYTES", 10 * 1024 * 1024),
        help="max JSON request payload size in bytes",
    )
    parser.add_argument(
        "--max-audio-bytes",
        type=int,
        default=parse_env_int("KEYSTREL_MAX_AUDIO_BYTES", 6 * 1024 * 1024),
        help="max decoded audio bytes for remote payloads",
    )


def _add_model_args(parser):
    parser.add_argument(
        "--model",
        default=get_env("KEYSTREL_MODEL", "distil-large-v3"),
        help="faster-whisper model name",
    )
    parser.add_argument(
        "--device",
        default=get_env("KEYSTREL_DEVICE", "cuda"),
        choices=["cuda", "cpu", "auto"],
        help="inference device",
    )
    parser.add_argument(
        "--compute-type",
        default=get_env("KEYSTREL_COMPUTE_TYPE", "float16"),
        help="ct2 compute type (float16, int8_float16, int8, ...)",
    )


def _add_transcription_default_args(parser):
    parser.add_argument(
        "--beam-size",
        type=int,
        default=parse_env_int("KEYSTREL_BEAM_SIZE", 1),
        help="default beam size",
    )
    parser.add_argument(
        "--best-of",
        type=int,
        default=parse_env_int("KEYSTREL_BEST_OF", 1),
        help="default best-of",
    )
    parser.add_argument(
        "--vad-filter",
        type=parse_bool,
        default=parse_env_bool("KEYSTREL_VAD_FILTER", True),
        help="enable built-in VAD filter by default",
    )
    parser.add_argument(
        "--language",
        default=get_env("KEYSTREL_LANGUAGE", ""),
        help="optional default language code, e.g. en",
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Warm faster-whisper daemon over Unix socket and optional TCP")
    _add_transport_args(parser)
    _add_limit_args(parser)
    _add_model_args(parser)
    _add_transcription_default_args(parser)
    return parser.parse_args()


def _normalize_runtime_args(args):
    args.socket = args.socket.strip()
    args.tcp_listen = args.tcp_listen.strip()
    args.server_token = args.server_token.strip()
    args.max_request_bytes = max(1024, args.max_request_bytes)
    args.max_audio_bytes = max(1024, args.max_audio_bytes)


def _validate_runtime_args(args):
    if args.tcp_listen and not args.server_token:
        print(
            "[keystrel-daemon] KEYSTREL_SERVER_TOKEN is required when TCP listener is enabled",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.tcp_port <= 0 or args.tcp_port > 65535:
        print(f"[keystrel-daemon] invalid tcp port: {args.tcp_port}", file=sys.stderr)
        sys.exit(2)


def _build_default_options(args):
    default_options = {
        "beam_size": args.beam_size,
        "best_of": args.best_of,
        "vad_filter": args.vad_filter,
        "condition_on_previous_text": False,
    }
    if args.language.strip():
        default_options["language"] = args.language.strip()
    return default_options


def _create_unix_server(args, model, default_options):
    socket_path = Path(args.socket).expanduser()
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        home_dir = Path.home().resolve()
        socket_dir = socket_path.parent.resolve()
        if socket_dir == home_dir or home_dir in socket_dir.parents:
            socket_path.parent.chmod(0o700)
    except OSError as exc:
        print(f"[keystrel-daemon] warning: could not chmod socket dir: {exc}", file=sys.stderr)

    try:
        remove_existing_socket(socket_path)
    except RuntimeError as exc:
        print(f"[keystrel-daemon] {exc}", file=sys.stderr)
        sys.exit(1)

    old_umask = os.umask(0o077)
    try:
        unix_server = KeystrelUnixServer(
            socket_path,
            model,
            default_options,
            args.max_request_bytes,
            args.max_audio_bytes,
        )
    finally:
        os.umask(old_umask)

    try:
        socket_path.chmod(0o600)
    except OSError as exc:
        print(f"[keystrel-daemon] warning: could not chmod socket file: {exc}", file=sys.stderr)

    return unix_server


def _create_tcp_server(args, model, default_options):
    return KeystrelTCPServer(
        args.tcp_listen,
        args.tcp_port,
        model,
        default_options,
        args.max_request_bytes,
        args.max_audio_bytes,
        args.server_token,
    )


def _build_servers(args, model, default_options):
    servers = []

    if args.socket:
        servers.append(_create_unix_server(args, model, default_options))

    if args.tcp_listen:
        servers.append(_create_tcp_server(args, model, default_options))

    if not servers:
        print(
            "[keystrel-daemon] no transports enabled; set KEYSTREL_SOCKET and/or KEYSTREL_TCP_LISTEN",
            file=sys.stderr,
        )
        sys.exit(2)

    return servers


def _install_shutdown_handlers(stop_event, servers):
    def shutdown_handler(signum, frame):  # noqa: ARG001
        if stop_event.is_set():
            return
        print(f"[keystrel-daemon] received signal {signum}, shutting down", file=sys.stderr)
        stop_event.set()
        for server in servers:
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)


def _start_servers(servers):
    for server in servers:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        thread.start()

        if server.transport == "unix":
            print(f"[keystrel-daemon] ready socket={server.socket_path}", file=sys.stderr)
        else:
            print(
                f"[keystrel-daemon] ready tcp={server.listen_host}:{server.listen_port} "
                f"max_request_bytes={server.max_request_bytes}",
                file=sys.stderr,
            )


def _cleanup_servers(servers):
    for server in servers:
        try:
            server.server_close()
        except OSError:
            pass

        if server.transport == "unix":
            try:
                if server.socket_path.exists():
                    if server.socket_path.is_socket():
                        server.socket_path.unlink()
                    else:
                        print(
                            f"[keystrel-daemon] warning: leaving non-socket path untouched: {server.socket_path}",
                            file=sys.stderr,
                        )
            except OSError as exc:
                print(f"[keystrel-daemon] warning: socket cleanup failed: {exc}", file=sys.stderr)


def main():
    args = parse_args()
    _normalize_runtime_args(args)
    _validate_runtime_args(args)

    print(
        f"[keystrel-daemon] loading model={args.model} device={args.device} compute_type={args.compute_type}",
        file=sys.stderr,
    )
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    default_options = _build_default_options(args)
    servers = _build_servers(args, model, default_options)

    stop_event = threading.Event()
    _install_shutdown_handlers(stop_event, servers)
    _start_servers(servers)

    try:
        while not stop_event.is_set():
            time.sleep(0.25)
    finally:
        _cleanup_servers(servers)
        print("[keystrel-daemon] stopped", file=sys.stderr)


if __name__ == "__main__":
    main()

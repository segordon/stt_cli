#!/usr/bin/env python3

import argparse
import json
import os
import signal
import socketserver
import stat
import sys
import threading
import time
from pathlib import Path
from typing import cast

from faster_whisper import WhisperModel  # type: ignore[import-not-found]


def parse_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def parse_env_int(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[stt-daemon] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return parse_bool(raw)
    except ValueError:
        print(f"[stt-daemon] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def remove_existing_socket(path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return

    if stat.S_ISSOCK(st.st_mode):
        path.unlink()
        return

    raise RuntimeError(f"refusing to remove non-socket path: {path}")


class STTServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path, model, default_options):
        super().__init__(str(socket_path), STTHandler)
        self.model = model
        self.default_options = default_options


class STTHandler(socketserver.StreamRequestHandler):
    def send_json(self, payload):
        self.wfile.write((json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8"))

    def handle(self):
        line = self.rfile.readline(1024 * 1024)
        if not line:
            return

        try:
            request = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self.send_json({"ok": False, "error": f"invalid JSON: {exc}"})
            return

        audio_path = request.get("audio_path")
        if not isinstance(audio_path, str) or not audio_path:
            self.send_json({"ok": False, "error": "missing required field: audio_path"})
            return

        audio_file = Path(audio_path).expanduser()
        if not audio_file.is_file():
            self.send_json({"ok": False, "error": f"audio file does not exist: {audio_file}"})
            return

        server = cast(STTServer, self.server)
        options = dict(server.default_options)
        for key in ("language", "task"):
            if key in request and isinstance(request[key], str) and request[key].strip():
                options[key] = request[key].strip()

        if "vad_filter" in request:
            try:
                options["vad_filter"] = parse_bool(request["vad_filter"])
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)})
                return

        for key in ("beam_size", "best_of"):
            if key in request:
                try:
                    options[key] = int(request[key])
                except (TypeError, ValueError):
                    self.send_json({"ok": False, "error": f"invalid integer for {key}"})
                    return

        started_at = time.perf_counter()
        try:
            segments, info = server.model.transcribe(str(audio_file), **options)
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:  # noqa: BLE001
            self.send_json({"ok": False, "error": f"transcription failed: {exc}"})
            return

        elapsed_s = time.perf_counter() - started_at
        response = {
            "ok": True,
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "elapsed_s": round(elapsed_s, 3),
        }
        self.send_json(response)


def parse_args():
    parser = argparse.ArgumentParser(description="Warm faster-whisper daemon over a Unix socket")
    parser.add_argument(
        "--socket",
        default=os.environ.get("STT_SOCKET", "~/.cache/stt/faster-whisper.sock"),
        help="Unix socket path",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("STT_MODEL", "distil-large-v3"),
        help="faster-whisper model name",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("STT_DEVICE", "cuda"),
        choices=["cuda", "cpu", "auto"],
        help="inference device",
    )
    parser.add_argument(
        "--compute-type",
        default=os.environ.get("STT_COMPUTE_TYPE", "float16"),
        help="ct2 compute type (float16, int8_float16, int8, ...)",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=parse_env_int("STT_BEAM_SIZE", 1),
        help="default beam size",
    )
    parser.add_argument(
        "--best-of",
        type=int,
        default=parse_env_int("STT_BEST_OF", 1),
        help="default best-of",
    )
    parser.add_argument(
        "--vad-filter",
        type=parse_bool,
        default=parse_env_bool("STT_VAD_FILTER", True),
        help="enable built-in VAD filter by default",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("STT_LANGUAGE", ""),
        help="optional default language code, e.g. en",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    socket_path = Path(args.socket).expanduser()
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        home_dir = Path.home().resolve()
        socket_dir = socket_path.parent.resolve()
        if socket_dir == home_dir or home_dir in socket_dir.parents:
            socket_path.parent.chmod(0o700)
    except OSError as exc:
        print(f"[stt-daemon] warning: could not chmod socket dir: {exc}", file=sys.stderr)
    try:
        remove_existing_socket(socket_path)
    except RuntimeError as exc:
        print(f"[stt-daemon] {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"[stt-daemon] loading model={args.model} device={args.device} compute_type={args.compute_type}",
        file=sys.stderr,
    )
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    options = {
        "beam_size": args.beam_size,
        "best_of": args.best_of,
        "vad_filter": args.vad_filter,
        "condition_on_previous_text": False,
    }
    if args.language.strip():
        options["language"] = args.language.strip()

    old_umask = os.umask(0o077)
    try:
        server = STTServer(socket_path, model, options)
    finally:
        os.umask(old_umask)

    try:
        socket_path.chmod(0o600)
    except OSError as exc:
        print(f"[stt-daemon] warning: could not chmod socket file: {exc}", file=sys.stderr)

    def shutdown_handler(signum, frame):  # noqa: ARG001
        print(f"[stt-daemon] received signal {signum}, shutting down", file=sys.stderr)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(f"[stt-daemon] ready socket={socket_path}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        try:
            if socket_path.exists():
                if socket_path.is_socket():
                    socket_path.unlink()
                else:
                    print(
                        f"[stt-daemon] warning: leaving non-socket path untouched: {socket_path}",
                        file=sys.stderr,
                    )
        except OSError as exc:
            print(f"[stt-daemon] warning: socket cleanup failed: {exc}", file=sys.stderr)
        print("[stt-daemon] stopped", file=sys.stderr)


if __name__ == "__main__":
    main()

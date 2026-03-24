#!/usr/bin/env python3

import argparse
import fcntl
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd  # type: ignore[import-not-found]
import soundfile as sf  # type: ignore[import-not-found]

try:
    import webrtcvad  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    webrtcvad = None


WEBRTCVAD_SAMPLE_RATES = {8000, 16000, 32000, 48000}
WEBRTCVAD_FRAME_MS = {10, 20, 30}


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
        print(f"[stt-client] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[stt-client] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return parse_bool(raw)
    except ValueError:
        print(f"[stt-client] invalid {name}={raw!r}, using default {default}", file=sys.stderr)
        return default


def parse_args():
    parser = argparse.ArgumentParser(
        description="Record microphone audio and request transcription from stt-daemon"
    )
    parser.add_argument(
        "--socket",
        default=os.environ.get("STT_SOCKET", "~/.cache/stt/faster-whisper.sock"),
        help="Unix socket path",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=parse_env_int("STT_SAMPLE_RATE", 16000),
        help="input sample rate",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=1,
        help="number of input channels",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=parse_env_float("STT_MAX_SECONDS", 12.0),
        help="hard capture limit",
    )
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=parse_env_float("STT_MIN_SECONDS", 0.35),
        help="minimum capture length before auto stop",
    )
    parser.add_argument(
        "--silence-seconds",
        type=float,
        default=parse_env_float("STT_SILENCE_SECONDS", 0.9),
        help="auto stop after this much trailing silence",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=parse_env_float("STT_THRESHOLD", 0.015),
        help="RMS threshold for voice activity",
    )
    parser.add_argument(
        "--block-seconds",
        type=float,
        default=0.08,
        help="capture block duration",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("STT_INPUT_DEVICE"),
        help="optional input device name/id",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("STT_LANGUAGE", ""),
        help="optional language code, e.g. en",
    )
    parser.add_argument(
        "--vad-filter",
        type=parse_bool,
        default=None,
        help="override daemon VAD filter setting",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=None,
        help="override daemon beam size",
    )
    parser.add_argument(
        "--best-of",
        type=int,
        default=None,
        help="override daemon best-of",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print full JSON response",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print diagnostics to stderr",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="list audio devices and exit",
    )
    parser.add_argument(
        "--mute-output",
        action=argparse.BooleanOptionalAction,
        default=parse_env_bool("STT_MUTE_OUTPUT", True),
        help="mute all audio output sinks while recording",
    )
    parser.add_argument(
        "--webrtcvad",
        action=argparse.BooleanOptionalAction,
        default=parse_env_bool("STT_WEBRTCVAD", True),
        help="use WebRTC VAD to suppress steady background noise",
    )
    parser.add_argument(
        "--webrtcvad-mode",
        type=int,
        choices=(0, 1, 2, 3),
        default=parse_env_int("STT_WEBRTCVAD_MODE", 2),
        help="WebRTC VAD aggressiveness (3 is most aggressive)",
    )
    parser.add_argument(
        "--webrtcvad-frame-ms",
        type=int,
        choices=(10, 20, 30),
        default=parse_env_int("STT_WEBRTCVAD_FRAME_MS", 20),
        help="WebRTC VAD frame size in milliseconds",
    )
    parser.add_argument(
        "--speech-ratio",
        type=float,
        default=parse_env_float("STT_SPEECH_RATIO", 0.60),
        help="minimum voiced-frame ratio in a block to count as speech",
    )
    parser.add_argument(
        "--start-speech-chunks",
        type=int,
        default=parse_env_int("STT_START_SPEECH_CHUNKS", 2),
        help="consecutive speech-positive blocks required to start capture",
    )
    parser.add_argument(
        "--pre-roll-seconds",
        type=float,
        default=parse_env_float("STT_PRE_ROLL_SECONDS", 0.35),
        help="seconds of audio to retain before speech start",
    )
    parser.add_argument(
        "--noise-multiplier",
        type=float,
        default=parse_env_float("STT_NOISE_MULTIPLIER", 2.5),
        help="RMS fallback multiplier over measured noise floor",
    )
    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=parse_env_float("STT_SOCKET_TIMEOUT", 20.0),
        help="seconds before daemon socket connect/read timeout",
    )

    args = parser.parse_args()
    args.sample_rate = max(1, args.sample_rate)
    args.max_seconds = max(0.1, args.max_seconds)
    args.min_seconds = max(0.0, args.min_seconds)
    if args.min_seconds > args.max_seconds:
        args.min_seconds = args.max_seconds
    args.silence_seconds = max(0.0, args.silence_seconds)
    args.block_seconds = max(0.01, args.block_seconds)
    args.threshold = max(0.0, args.threshold)
    args.speech_ratio = max(0.0, min(1.0, args.speech_ratio))
    args.start_speech_chunks = max(1, args.start_speech_chunks)
    args.pre_roll_seconds = max(0.0, args.pre_roll_seconds)
    args.noise_multiplier = max(1.0, args.noise_multiplier)
    args.socket_timeout = max(0.1, args.socket_timeout)
    return args


def list_output_sinks():
    result = subprocess.run(
        ["pactl", "list", "short", "sinks"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or "pactl list short sinks failed")

    sinks = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) == 1:
            parts = line.split()
        if not parts:
            continue
        sinks.append(parts[0])
    return sinks


def get_sink_mute_state(sink):
    result = subprocess.run(
        ["pactl", "get-sink-mute", sink],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"failed to read mute state for sink {sink}")

    output = result.stdout.strip().lower()
    if output.endswith("yes"):
        return True
    if output.endswith("no"):
        return False
    raise RuntimeError(f"unexpected pactl output for sink {sink}: {result.stdout.strip()}")


def set_sink_mute_state(sink, muted):
    result = subprocess.run(
        ["pactl", "set-sink-mute", sink, "1" if muted else "0"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"failed to set mute={muted} for sink {sink}")


def mute_output_during_capture(args):
    if not args.mute_output:
        return {}

    if not shutil.which("pactl"):
        if args.verbose:
            print(
                "[stt-client] --mute-output requested but pactl is not installed",
                file=sys.stderr,
            )
        return {}

    sink_states = {}
    try:
        sinks = list_output_sinks()
        for sink in sinks:
            was_muted = get_sink_mute_state(sink)
            sink_states[sink] = was_muted
            if not was_muted:
                set_sink_mute_state(sink, True)
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            print(f"[stt-client] output mute setup failed: {exc}", file=sys.stderr)
        return sink_states

    if args.verbose and sink_states:
        print(f"[stt-client] muted {len(sink_states)} output sink(s)", file=sys.stderr)
    return sink_states


def restore_output_mute(args, sink_states):
    if not sink_states:
        return

    errors = 0
    for sink, was_muted in sink_states.items():
        try:
            set_sink_mute_state(sink, was_muted)
        except Exception:  # noqa: BLE001
            errors += 1

    if args.verbose:
        restored = len(sink_states) - errors
        print(
            f"[stt-client] restored mute state for {restored}/{len(sink_states)} sink(s)",
            file=sys.stderr,
        )


def acquire_client_lock(args):
    lock_path = Path(
        os.environ.get("STT_CLIENT_LOCK", "~/.cache/stt/stt-client.lock")
    ).expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        if args.verbose:
            print("[stt-client] another capture is already running", file=sys.stderr)
        return None
    return lock_file


def build_webrtc_vad(args):
    if not args.webrtcvad:
        return None
    if webrtcvad is None:
        if args.verbose:
            print("[stt-client] WebRTC VAD unavailable, using RMS fallback", file=sys.stderr)
        return None
    if args.sample_rate not in WEBRTCVAD_SAMPLE_RATES:
        if args.verbose:
            print(
                f"[stt-client] sample rate {args.sample_rate} unsupported for WebRTC VAD; "
                "using RMS fallback",
                file=sys.stderr,
            )
        return None
    if args.webrtcvad_frame_ms not in WEBRTCVAD_FRAME_MS:
        if args.verbose:
            print(
                f"[stt-client] frame size {args.webrtcvad_frame_ms}ms unsupported for WebRTC VAD; "
                "using RMS fallback",
                file=sys.stderr,
            )
        return None

    try:
        return webrtcvad.Vad(args.webrtcvad_mode)
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            print(f"[stt-client] failed to initialize WebRTC VAD: {exc}", file=sys.stderr)
        return None


def speech_ratio_in_chunk(chunk, args, vad):
    if vad is None:
        return None

    mono = chunk
    if mono.ndim > 1:
        mono = np.mean(mono, axis=1)
    mono = np.clip(mono, -1.0, 1.0)
    pcm16 = (mono * 32767.0).astype(np.int16)

    frame_samples = int(args.sample_rate * args.webrtcvad_frame_ms / 1000)
    if frame_samples <= 0:
        return None

    total_frames = 0
    voiced_frames = 0
    end = len(pcm16) - frame_samples + 1
    if end <= 0:
        return 0.0

    for start in range(0, end, frame_samples):
        frame = pcm16[start : start + frame_samples]
        if len(frame) != frame_samples:
            continue
        total_frames += 1
        try:
            if vad.is_speech(frame.tobytes(), args.sample_rate):
                voiced_frames += 1
        except Exception:  # noqa: BLE001
            return None

    if total_frames == 0:
        return 0.0
    return voiced_frames / total_frames


def record_until_silence(args):
    blocksize = max(1, int(args.sample_rate * args.block_seconds))
    audio_queue = queue.Queue()
    chunks = []
    pre_roll_chunk_count = int(args.pre_roll_seconds / args.block_seconds)
    pre_roll_chunks = deque(maxlen=pre_roll_chunk_count) if pre_roll_chunk_count > 0 else None
    started_voice = False
    started_at = time.monotonic()
    last_voice_at = None
    speech_streak = 0
    noise_floor = None
    vad = build_webrtc_vad(args)

    def callback(indata, frames, time_info, status):  # noqa: ARG001
        if status and args.verbose:
            print(f"[stt-client] audio status: {status}", file=sys.stderr)
        audio_queue.put(indata.copy())

    stream_kwargs = {
        "samplerate": args.sample_rate,
        "channels": args.channels,
        "dtype": "float32",
        "blocksize": blocksize,
        "callback": callback,
    }
    if args.device is not None:
        stream_kwargs["device"] = args.device

    if args.verbose:
        print(
            "[stt-client] recording "
            f"max={args.max_seconds}s min={args.min_seconds}s silence={args.silence_seconds}s "
            f"threshold={args.threshold} webrtcvad={'on' if vad is not None else 'off'}",
            file=sys.stderr,
        )

    with sd.InputStream(**stream_kwargs):
        while True:
            now = time.monotonic()
            elapsed = now - started_at
            if elapsed >= args.max_seconds:
                break

            try:
                chunk = audio_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            rms = float(np.sqrt(np.mean(np.square(chunk))))
            speech_ratio = speech_ratio_in_chunk(chunk, args, vad)
            if speech_ratio is not None:
                is_voice = speech_ratio >= args.speech_ratio
            else:
                if not started_voice:
                    if noise_floor is None:
                        noise_floor = rms
                    else:
                        noise_floor = 0.9 * noise_floor + 0.1 * rms
                dynamic_threshold = args.threshold
                if noise_floor is not None:
                    dynamic_threshold = max(dynamic_threshold, noise_floor * args.noise_multiplier)
                is_voice = rms >= dynamic_threshold

            if is_voice:
                speech_streak += 1
            else:
                speech_streak = 0

            if not started_voice:
                if pre_roll_chunks is not None:
                    pre_roll_chunks.append(chunk)
                if speech_streak >= args.start_speech_chunks:
                    started_voice = True
                    last_voice_at = now
                    if pre_roll_chunks is not None:
                        chunks.extend(pre_roll_chunks)
                        pre_roll_chunks.clear()
                continue

            chunks.append(chunk)
            if is_voice:
                last_voice_at = now

            if (
                started_voice
                and elapsed >= args.min_seconds
                and last_voice_at is not None
                and (now - last_voice_at) >= args.silence_seconds
            ):
                break

    if not chunks or not started_voice:
        return np.empty((0, args.channels), dtype=np.float32)
    return np.concatenate(chunks, axis=0)


def send_request(socket_path, payload, timeout_s):
    data = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        try:
            sock.connect(str(socket_path))
            sock.sendall(data)
            response = b""
            while not response.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        except socket.timeout as exc:
            raise TimeoutError(f"daemon request timed out after {timeout_s:.1f}s") from exc

    if not response:
        raise RuntimeError("empty response from daemon")
    return json.loads(response.decode("utf-8"))


def main():
    args = parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    lock_file = acquire_client_lock(args)
    if lock_file is None:
        return

    try:
        socket_path = Path(args.socket).expanduser()
        if not socket_path.exists():
            print(
                f"[stt-client] daemon socket not found: {socket_path}\n"
                "[stt-client] start it with: systemctl --user start stt-daemon",
                file=sys.stderr,
            )
            sys.exit(2)

        sink_states = {}
        try:
            sink_states = mute_output_during_capture(args)
            audio = record_until_silence(args)
        except Exception as exc:  # noqa: BLE001
            print(f"[stt-client] microphone capture failed: {exc}", file=sys.stderr)
            sys.exit(3)
        finally:
            restore_output_mute(args, sink_states)

        if audio.size == 0:
            print("", end="")
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        try:
            sf.write(str(wav_path), audio, args.sample_rate)
            payload = {"audio_path": str(wav_path)}
            if args.language.strip():
                payload["language"] = args.language.strip()
            if args.vad_filter is not None:
                payload["vad_filter"] = args.vad_filter
            if args.beam_size is not None:
                payload["beam_size"] = args.beam_size
            if args.best_of is not None:
                payload["best_of"] = args.best_of

            response = send_request(socket_path, payload, args.socket_timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[stt-client] request failed: {exc}", file=sys.stderr)
            sys.exit(4)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not response.get("ok"):
            print(f"[stt-client] daemon error: {response.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(5)

        if args.verbose:
            print(
                f"[stt-client] elapsed={response.get('elapsed_s')}s language={response.get('language')}",
                file=sys.stderr,
            )

        if args.json:
            print(json.dumps(response, ensure_ascii=True))
        else:
            print(response.get("text", ""))
    finally:
        lock_file.close()


if __name__ == "__main__":
    main()

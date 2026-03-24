# STT Quickstart and Operating Guide

This project provides local, GPU-accelerated speech-to-text for Ubuntu GNOME terminal workflows, with push-to-talk behavior that works well for interactive prompts.

For deep implementation history and agent handoff context, read `AGENTS.md`.
For command-only reference, use `CHEATSHEET.md`.

## What You Get

- Persistent `faster-whisper` backend on NVIDIA GPU for low latency after warm-up.
- Microphone capture client with auto-stop on trailing silence.
- Optional mute of all audio output during listening to reduce feedback contamination.
- Push-to-talk helper that types transcript text directly into the focused X11 window.
- GNOME global hotkey integration for practical day-to-day use.

## Current Runtime State

- Desktop/session: GNOME on X11.
- Backend model defaults: English-focused `large-v3` with stronger decoding search.
- Keybinding: `Ctrl+grave` (`<Primary>grave`) runs `/home/user/.local/bin/stt-ptt`.

## High-Level Architecture

There are 3 layers:

1. `stt-daemon` (always-on backend)
   - Loads Whisper model once and keeps it warm in VRAM.
   - Accepts requests over Unix socket.
   - Returns transcript JSON.

2. `stt-client` (capture/transcribe command)
   - Records mic audio.
   - Applies speech gating to reject environmental noise.
   - Sends WAV path to daemon.
   - Prints transcript to stdout.

3. `stt-ptt` (desktop typing integration)
   - Calls `stt-client`.
   - Cleans transcript text.
   - Types text into active window via `xdotool`.
   - Optional Enter key submit.

## File Layout

Active runtime files (source of truth):

- `/home/user/.local/lib/stt/stt_daemon.py`
- `/home/user/.local/lib/stt/stt_client.py`
- `/home/user/.local/bin/stt-daemon`
- `/home/user/.local/bin/stt-client`
- `/home/user/.local/bin/stt-ptt`
- `/home/user/.config/stt-daemon.env`
- `/home/user/.config/systemd/user/stt-daemon.service`
- `/home/user/.venvs/faster-whisper/env.sh`

Development mirror:

- `/home/user/Documents/stt/*`

Important: mirror wrapper scripts currently execute the active runtime paths under `/home/user/.local`.

## Prerequisites and Installed Dependencies

APT packages used:

- `libportaudio2`
- `pulseaudio-utils` (for `pactl`)
- `xdotool`
- `ffmpeg` (already present and useful for tests)

Python packages in STT venv include:

- `faster-whisper`
- `ctranslate2`
- `sounddevice`
- `soundfile`
- `webrtcvad-wheels`
- `nvidia-cublas-cu12`
- `nvidia-cudnn-cu12`

## Service Management

Daemon service is managed by user systemd.

Check status:

```bash
systemctl --user status stt-daemon
```

Restart service:

```bash
systemctl --user restart stt-daemon
```

View logs live:

```bash
journalctl --user -u stt-daemon -f
```

## Quickstart (Daily Usage)

1. Ensure daemon is running:

```bash
systemctl --user status stt-daemon
```

2. Optional device check:

```bash
stt-client --list-devices
```

3. Test direct transcription path:

```bash
stt-client --verbose
```

4. Use push-to-talk in terminal:

- Focus terminal input.
- Press `Ctrl+grave` once.
- Speak naturally.
- Pause briefly at sentence end.
- Transcript should be typed into the focused window.

## How `stt-client` Detects Speech

The client uses layered gating to avoid triggering on fan noise/hum.

1. WebRTC VAD frame classification
   - Audio is split into small frames (default 20 ms).
   - VAD marks each frame as speech/non-speech.

2. Speech ratio threshold per block
   - For each block, voiced frame ratio must reach `--speech-ratio`.

3. Consecutive speech chunks requirement
   - Requires `--start-speech-chunks` positive blocks before capture starts.

4. Pre-roll buffering
   - Keeps short audio history (`--pre-roll-seconds`) so first words are not cut off.

5. Trailing silence stop
   - Once speech started, stops after `--silence-seconds` of inactivity.

Fallback behavior:

- If WebRTC VAD is unavailable or unsupported at the selected sample rate, it falls back to adaptive RMS thresholding.

## How Output Muting Works

When enabled (default), `stt-client`:

1. Enumerates output sinks with `pactl`.
2. Stores each sink's current mute state.
3. Mutes sinks for the capture window.
4. Restores every sink to its original mute state in a `finally` block.

This prevents speaker audio from being interpreted as mic speech and reduces false triggers.

## Concurrency and Repeat Protection

Two lock layers prevent race conditions and repeated delayed output:

- `stt-client` lock (`~/.cache/stt/stt-client.lock`)
  - Prevents overlapping captures from racing sink state.

- `stt-ptt` lock + debounce (`stt-ptt.lock`, `stt-ptt.last`)
  - Prevents key auto-repeat from launching many concurrent jobs.

## Configuration

### Daemon configuration file

Edit `/home/user/.config/stt-daemon.env`.

Current defaults:

```dotenv
STT_MODEL=large-v3
STT_DEVICE=cuda
STT_COMPUTE_TYPE=float16
STT_BEAM_SIZE=5
STT_BEST_OF=5
STT_VAD_FILTER=1
STT_LANGUAGE=en
STT_SOCKET=/home/user/.cache/stt/faster-whisper.sock
```

After edits, restart daemon:

```bash
systemctl --user restart stt-daemon
```

### Client tuning flags (most important)

- `--webrtcvad` / `--no-webrtcvad`
- `--webrtcvad-mode {0,1,2,3}` (higher = stricter)
- `--speech-ratio` (higher = stricter)
- `--start-speech-chunks` (higher = stricter)
- `--pre-roll-seconds` (larger = safer word onset)
- `--silence-seconds` (larger = waits longer before stopping)
- `--threshold` and `--noise-multiplier` (fallback RMS path)
- `--socket-timeout` (bounds daemon request wait time)

### PTT behavior env vars

- `STT_PTT_DEBOUNCE_MS` (default `1200`)
- `STT_TYPE_DELAY_MS` (default `1`)
- `STT_PTT_SEND_ENTER` (`1` to press Enter after typing)
- `STT_CLIENT_BIN` (override client path)

### Wrapper override env vars

Useful if runtime files are relocated:

- `STT_ENV_FILE` (used by `stt-client` and `stt-daemon` wrappers)
- `STT_CLIENT_PY` (override Python client script path)
- `STT_DAEMON_PY` (override Python daemon script path)
- `STT_SOCKET_TIMEOUT` (default client daemon-request timeout)

## Tuning Recipes

### Balanced default (current)

Good mix of sensitivity and noise rejection:

```bash
stt-client --verbose --webrtcvad-mode 2 --speech-ratio 0.60 --start-speech-chunks 2
```

### Noisy environment (stricter)

```bash
stt-client --verbose --webrtcvad-mode 3 --speech-ratio 0.72 --start-speech-chunks 3
```

### If phrases are being missed (more permissive)

```bash
stt-client --verbose --webrtcvad-mode 1 --speech-ratio 0.50 --start-speech-chunks 1
```

### Longer pause before auto-stop

```bash
stt-client --silence-seconds 1.2
```

### Use a specific microphone

```bash
stt-client --list-devices
stt-client --device 6 --verbose
```

### Disable mute for one run

```bash
stt-client --no-mute-output
```

## GNOME Keybinding Notes

Current binding points to:

- `/home/user/.local/bin/stt-ptt`

Check full binding object:

```bash
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/stt-ptt/ command
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/stt-ptt/ binding
```

Conflict note:

- No known GNOME global conflict was detected for `<Primary>grave` at setup time.
- Some applications may still have app-local meaning for `Ctrl+grave`.

## Troubleshooting

No transcript appears:

- Check daemon: `systemctl --user status stt-daemon`
- Check logs: `journalctl --user -u stt-daemon -n 80 --no-pager`
- Test client directly: `stt-client --verbose`
- Verify socket exists: `/home/user/.cache/stt/faster-whisper.sock`
- Expected secure permissions: socket dir `drwx------`, socket file `srw-------`

PTT key does nothing:

- Confirm command path in GNOME binding is `/home/user/.local/bin/stt-ptt`.
- Confirm `xdotool` exists: `xdotool -v`.
- Confirm session is X11: `echo "$XDG_SESSION_TYPE"`.

Too many false activations:

- Raise strictness:

```bash
stt-client --webrtcvad-mode 3 --speech-ratio 0.72 --start-speech-chunks 3
```

Speech frequently missed:

- Relax strictness:

```bash
stt-client --webrtcvad-mode 1 --speech-ratio 0.50 --start-speech-chunks 1
```

Audio output remains muted:

- Inspect sink states:

```bash
pactl list short sinks
pactl get-sink-mute <sink-id>
```

- Emergency unmute all sinks:

```bash
for s in $(pactl list short sinks | awk '{print $1}'); do pactl set-sink-mute "$s" 0; done
```

## Development Workflow Notes

When modifying behavior:

1. Edit active runtime files under `/home/user/.local/...`.
2. Validate Python syntax:

```bash
source /home/user/.venvs/faster-whisper/env.sh
python -m py_compile /home/user/.local/lib/stt/stt_client.py /home/user/.local/lib/stt/stt_daemon.py
```

3. Restart and verify daemon:

```bash
systemctl --user restart stt-daemon
systemctl --user status stt-daemon
```

4. Run smoke test:

```bash
stt-client --verbose --max-seconds 1.5
```

5. Sync mirror files into `/home/user/Documents/stt/*` when needed.

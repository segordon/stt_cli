# STT Cheat Sheet

Fast command reference for the local GPU STT stack.

## Daily Use

Check daemon:

```bash
systemctl --user status stt-daemon
```

Restart daemon:

```bash
systemctl --user restart stt-daemon
```

Tail daemon logs:

```bash
journalctl --user -u stt-daemon -f
```

Basic STT test:

```bash
stt-client --verbose
```

List mic devices:

```bash
stt-client --list-devices
```

Use specific mic:

```bash
stt-client --device 6 --verbose
```

## Push To Talk

Current GNOME hotkey:

- `Ctrl+grave` (`<Primary>grave`)

Binding points to:

- `/home/user/.local/bin/stt-ptt`

Check keybinding:

```bash
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/stt-ptt/ binding
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/stt-ptt/ command
```

Run PTT script manually:

```bash
stt-ptt
```

Auto-press Enter after typing:

```bash
STT_PTT_SEND_ENTER=1 stt-ptt
```

## Noise and Sensitivity Tuning

Current balanced profile:

```bash
stt-client --verbose --webrtcvad-mode 2 --speech-ratio 0.60 --start-speech-chunks 2
```

Stricter (more noise rejection):

```bash
stt-client --verbose --webrtcvad-mode 3 --speech-ratio 0.72 --start-speech-chunks 3
```

More permissive (catches quieter speech):

```bash
stt-client --verbose --webrtcvad-mode 1 --speech-ratio 0.50 --start-speech-chunks 1
```

Bound daemon request wait time:

```bash
stt-client --socket-timeout 15
```

Wait longer before stopping:

```bash
stt-client --silence-seconds 1.2
```

Disable output muting for one run:

```bash
stt-client --no-mute-output
```

## Common Fixes

Check session type (PTT typing currently expects X11):

```bash
echo "$XDG_SESSION_TYPE"
```

Verify socket exists:

```bash
ls -l /home/user/.cache/stt/faster-whisper.sock
```

Expected secure permissions:

- socket dir: `drwx------`
- socket file: `srw-------`

Emergency unmute all sinks:

```bash
for s in $(pactl list short sinks | awk '{print $1}'); do pactl set-sink-mute "$s" 0; done
```

Inspect sink mute states:

```bash
pactl list short sinks
pactl get-sink-mute <sink-id>
```

## Config Files

Daemon config:

- `/home/user/.config/stt-daemon.env`

Service unit:

- `/home/user/.config/systemd/user/stt-daemon.service`

Runtime scripts:

- `/home/user/.local/bin/stt-daemon`
- `/home/user/.local/bin/stt-client`
- `/home/user/.local/bin/stt-ptt`
- `/home/user/.local/lib/stt/stt_daemon.py`
- `/home/user/.local/lib/stt/stt_client.py`

Wrapper override env vars:

- `STT_ENV_FILE`
- `STT_CLIENT_PY`
- `STT_DAEMON_PY`
- `STT_SOCKET_TIMEOUT`

After changing daemon config:

```bash
systemctl --user restart stt-daemon
```

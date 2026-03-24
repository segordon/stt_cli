# Keystrel Migration Guide

This project renamed from legacy `stt-*` naming to `keystrel-*` naming.

## What Changed

- Commands: `stt-daemon` -> `keystrel-daemon`, `stt-client` -> `keystrel-client`, `stt-ptt` -> `keystrel-ptt`
- Python modules: `stt_daemon.py` -> `keystrel_daemon.py`, `stt_client.py` -> `keystrel_client.py`
- Preferred environment variables now use `KEYSTREL_` prefix

## Compatibility Behavior

- `KEYSTREL_*` is authoritative when both prefixes are set.
- Legacy `STT_*` is still read as a fallback.
- Using a legacy `STT_*` variable now prints a deprecation warning to stderr once per variable name.

## Recommended Migration Steps

1. Update shell profiles and env files from `STT_` to `KEYSTREL_`.
2. Update desktop launchers, scripts, and keybindings to call `keystrel-*` commands.
3. Restart user services after env updates:

```bash
systemctl --user daemon-reload
systemctl --user restart keystrel-daemon
```

4. Verify with:

```bash
keystrel-client --verbose
python -m unittest discover -s tests -v
```

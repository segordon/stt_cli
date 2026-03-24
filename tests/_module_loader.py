import importlib.util
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"


def _load_module(module_name, file_path, stub_modules):
    saved_modules = {}
    for name, module in stub_modules.items():
        saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = module

    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load module spec for {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, prior in saved_modules.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior


def load_client_module():
    stub_modules = {}
    for name in ("numpy", "sounddevice", "soundfile", "webrtcvad"):
        if name not in sys.modules:
            stub_modules[name] = types.ModuleType(name)

    return _load_module("keystrel_client_test", LIB_DIR / "keystrel_client.py", stub_modules)


def load_daemon_module():
    stub_modules = {}
    if "faster_whisper" not in sys.modules:
        faster_whisper = types.ModuleType("faster_whisper")

        class WhisperModel:  # pragma: no cover - import stub only
            def __init__(self, *args, **kwargs):
                pass

        setattr(faster_whisper, "WhisperModel", WhisperModel)
        stub_modules["faster_whisper"] = faster_whisper

    return _load_module("keystrel_daemon_test", LIB_DIR / "keystrel_daemon.py", stub_modules)

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tests._module_loader import load_client_module


stt_client = load_client_module()


def _args(**overrides):
    base = {
        "mute_output": True,
        "verbose": False,
        "start_chime": True,
        "chime_backend": "auto",
        "chime_cooldown_ms": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class ClientMuteControlTests(unittest.TestCase):
    def test_mute_output_disabled_returns_empty(self):
        args = _args(mute_output=False)
        self.assertEqual(stt_client.mute_output_during_capture(args), {})

    def test_mute_output_missing_pactl_returns_empty(self):
        args = _args()
        with mock.patch.object(stt_client.shutil, "which", return_value=None):
            self.assertEqual(stt_client.mute_output_during_capture(args), {})

    def test_mute_output_sets_only_unmuted_sinks(self):
        args = _args()
        with (
            mock.patch.object(stt_client.shutil, "which", return_value="/usr/bin/pactl"),
            mock.patch.object(stt_client, "list_output_sinks", return_value=["1", "2"]),
            mock.patch.object(stt_client, "get_sink_mute_state", side_effect=[False, True]),
            mock.patch.object(stt_client, "set_sink_mute_state") as set_mute,
        ):
            states = stt_client.mute_output_during_capture(args)

        self.assertEqual(states, {"1": False, "2": True})
        set_mute.assert_called_once_with("1", True)

    def test_mute_output_returns_partial_state_on_error(self):
        args = _args(verbose=True)
        with (
            mock.patch.object(stt_client.shutil, "which", return_value="/usr/bin/pactl"),
            mock.patch.object(stt_client, "list_output_sinks", return_value=["1", "2"]),
            mock.patch.object(
                stt_client,
                "get_sink_mute_state",
                side_effect=[False, RuntimeError("boom")],
            ),
            mock.patch.object(stt_client, "set_sink_mute_state") as set_mute,
        ):
            states = stt_client.mute_output_during_capture(args)

        self.assertEqual(states, {"1": False})
        set_mute.assert_called_once_with("1", True)

    def test_restore_output_mute_attempts_all_sinks(self):
        args = _args(verbose=True)
        with mock.patch.object(
            stt_client,
            "set_sink_mute_state",
            side_effect=[RuntimeError("fail one"), None],
        ) as set_mute:
            stt_client.restore_output_mute(args, {"1": False, "2": True})

        self.assertEqual(set_mute.call_count, 2)
        set_mute.assert_any_call("1", False)
        set_mute.assert_any_call("2", True)


class ClientChimeSelectionTests(unittest.TestCase):
    def test_play_start_chime_skips_when_disabled(self):
        args = _args(start_chime=False)
        with (
            mock.patch.object(stt_client, "_play_chime_pipewire") as pipewire,
            mock.patch.object(stt_client, "_play_chime_paplay") as paplay,
            mock.patch.object(stt_client, "_play_chime_sounddevice") as sounddevice,
            mock.patch.object(stt_client, "_play_chime_canberra") as canberra,
        ):
            stt_client.play_start_chime(args)

        pipewire.assert_not_called()
        paplay.assert_not_called()
        sounddevice.assert_not_called()
        canberra.assert_not_called()

    def test_play_start_chime_auto_stops_at_first_success(self):
        args = _args(chime_backend="auto")
        with (
            mock.patch.object(stt_client, "_play_chime_pipewire", return_value=True) as pipewire,
            mock.patch.object(stt_client, "_play_chime_paplay", return_value=False) as paplay,
            mock.patch.object(stt_client, "_play_chime_sounddevice", return_value=False) as sounddevice,
            mock.patch.object(stt_client, "_play_chime_canberra", return_value=False) as canberra,
        ):
            stt_client.play_start_chime(args)

        pipewire.assert_called_once()
        paplay.assert_not_called()
        sounddevice.assert_not_called()
        canberra.assert_not_called()

    def test_play_start_chime_pipewire_falls_back_to_paplay(self):
        args = _args(chime_backend="pipewire")
        with (
            mock.patch.object(stt_client, "_play_chime_pipewire", return_value=False) as pipewire,
            mock.patch.object(stt_client, "_play_chime_paplay", return_value=True) as paplay,
            mock.patch.object(stt_client, "_play_chime_sounddevice", return_value=False) as sounddevice,
            mock.patch.object(stt_client, "_play_chime_canberra", return_value=False) as canberra,
        ):
            stt_client.play_start_chime(args)

        pipewire.assert_called_once()
        paplay.assert_called_once()
        sounddevice.assert_not_called()
        canberra.assert_not_called()

    def test_play_start_chime_canberra_then_fallbacks(self):
        args = _args(chime_backend="canberra")
        with (
            mock.patch.object(stt_client, "_play_chime_canberra", return_value=False) as canberra,
            mock.patch.object(stt_client, "_play_chime_pipewire", return_value=False) as pipewire,
            mock.patch.object(stt_client, "_play_chime_paplay", return_value=True) as paplay,
            mock.patch.object(stt_client, "_play_chime_sounddevice", return_value=False) as sounddevice,
        ):
            stt_client.play_start_chime(args)

        canberra.assert_called_once()
        pipewire.assert_called_once()
        paplay.assert_called_once()
        sounddevice.assert_not_called()

    def test_play_start_chime_applies_cooldown_sleep(self):
        args = _args(chime_backend="auto", chime_cooldown_ms=25)
        with (
            mock.patch.object(stt_client, "_play_chime_pipewire", return_value=False),
            mock.patch.object(stt_client, "_play_chime_paplay", return_value=False),
            mock.patch.object(stt_client, "_play_chime_sounddevice", return_value=False),
            mock.patch.object(stt_client, "_play_chime_canberra", return_value=False),
            mock.patch.object(stt_client.time, "sleep") as sleep,
        ):
            stt_client.play_start_chime(args)

        sleep.assert_called_once_with(0.025)


class ClientLockTests(unittest.TestCase):
    def test_acquire_client_lock_is_nonblocking(self):
        args = _args(verbose=False)
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = os.path.join(tmp_dir, "client.lock")
            with mock.patch.dict(os.environ, {"STT_CLIENT_LOCK": lock_path}, clear=False):
                first = stt_client.acquire_client_lock(args)
                self.assertIsNotNone(first)

                second = stt_client.acquire_client_lock(args)
                self.assertIsNone(second)

                first.close()
                third = stt_client.acquire_client_lock(args)
                self.assertIsNotNone(third)
                third.close()


if __name__ == "__main__":
    unittest.main()

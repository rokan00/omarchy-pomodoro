#!/usr/bin/env python3
"""Unit tests for pomodoro.py: the pure phase-transition logic, the input
validation that guards it, and the private state directory every read and
write goes through.

Runs with the stdlib only: python3 scripts/test_pomodoro.py
"""
import json
import os
import re
import stat
import tempfile
import unittest

from pomodoro import (
    CONFIG_NAME,
    DEFAULT_CONFIG,
    MAX_FILE_BYTES,
    STATE_NAME,
    StateDir,
    StateDirError,
    advance_phase,
    child_environment,
    daemon_is_running,
    fresh_work_state,
    initial_state,
    public_snapshot,
    sanitize_config,
    sanitize_state,
    state_lock,
    tick,
    trusted_program,
    validate_config_updates,
)


def config(**overrides):
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(overrides)
    return cfg


class InitialAndFreshStateTests(unittest.TestCase):
    def test_initial_state_is_idle_and_shows_work_duration(self):
        cfg = config(work_minutes=25)
        state = initial_state(cfg)
        self.assertEqual(state["phase"], "idle")
        self.assertFalse(state["running"])
        self.assertEqual(state["remaining_seconds"], 25 * 60)
        self.assertEqual(state["cycle"], 1)

    def test_fresh_work_state_running_flag_is_passed_through(self):
        cfg = config(work_minutes=10)
        running_state = fresh_work_state(cfg, running=True)
        paused_state = fresh_work_state(cfg, running=False)
        self.assertTrue(running_state["running"])
        self.assertFalse(paused_state["running"])
        self.assertEqual(running_state["phase"], "work")
        self.assertEqual(running_state["remaining_seconds"], 10 * 60)
        self.assertEqual(running_state["cycle"], 1)


class AdvancePhaseTests(unittest.TestCase):
    def test_work_advances_to_short_break_when_not_on_long_break_cycle(self):
        cfg = config(cycles_until_long_break=4, break_minutes=5, long_break_minutes=15)
        state = {
            "phase": "work", "running": True, "remaining_seconds": 0,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state = advance_phase(state, cfg)
        self.assertEqual(new_state["phase"], "break")
        self.assertEqual(new_state["remaining_seconds"], 5 * 60)
        self.assertEqual(new_state["cycle"], 1)

    def test_work_advances_to_long_break_on_multiple_of_cycles_until_long_break(self):
        cfg = config(cycles_until_long_break=4, break_minutes=5, long_break_minutes=15)
        state = {
            "phase": "work", "running": True, "remaining_seconds": 0,
            "cycle": 4, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state = advance_phase(state, cfg)
        self.assertEqual(new_state["phase"], "long_break")
        self.assertEqual(new_state["remaining_seconds"], 15 * 60)
        self.assertEqual(new_state["cycle"], 4)

    def test_break_advances_to_work_and_increments_cycle(self):
        cfg = config(work_minutes=25)
        state = {
            "phase": "break", "running": True, "remaining_seconds": 0,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state = advance_phase(state, cfg)
        self.assertEqual(new_state["phase"], "work")
        self.assertEqual(new_state["remaining_seconds"], 25 * 60)
        self.assertEqual(new_state["cycle"], 2)

    def test_long_break_advances_to_work_and_increments_cycle(self):
        cfg = config(work_minutes=25)
        state = {
            "phase": "long_break", "running": True, "remaining_seconds": 0,
            "cycle": 4, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state = advance_phase(state, cfg)
        self.assertEqual(new_state["phase"], "work")
        self.assertEqual(new_state["cycle"], 5)

    def test_full_cycle_sequence_hits_long_break_every_nth_cycle(self):
        cfg = config(cycles_until_long_break=2, work_minutes=1, break_minutes=1, long_break_minutes=1)
        state = fresh_work_state(cfg, running=True)
        state["remaining_seconds"] = 0
        phases = []
        for _ in range(8):
            state = advance_phase(state, cfg)
            phases.append((state["phase"], state["cycle"]))
            state["remaining_seconds"] = 0

        self.assertEqual(
            phases,
            [
                ("break", 1),
                ("work", 2),
                ("long_break", 2),
                ("work", 3),
                ("break", 3),
                ("work", 4),
                ("long_break", 4),
                ("work", 5),
            ],
        )

    def test_running_flag_is_preserved_across_transition(self):
        cfg = config()
        state = {
            "phase": "work", "running": False, "remaining_seconds": 0,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state = advance_phase(state, cfg)
        self.assertFalse(new_state["running"])


class TickTests(unittest.TestCase):
    def test_tick_when_not_running_is_a_no_op(self):
        cfg = config()
        state = {
            "phase": "work", "running": False, "remaining_seconds": 100,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state, transitioned, completed_phase = tick(state, cfg)
        self.assertEqual(new_state, state)
        self.assertFalse(transitioned)
        self.assertIsNone(completed_phase)

    def test_tick_decrements_remaining_seconds(self):
        cfg = config()
        state = {
            "phase": "work", "running": True, "remaining_seconds": 100,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state, transitioned, completed_phase = tick(state, cfg)
        self.assertEqual(new_state["remaining_seconds"], 99)
        self.assertFalse(transitioned)
        self.assertIsNone(completed_phase)

    def test_tick_transitions_phase_when_reaching_zero(self):
        cfg = config(break_minutes=5)
        state = {
            "phase": "work", "running": True, "remaining_seconds": 1,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state, transitioned, completed_phase = tick(state, cfg)
        self.assertTrue(transitioned)
        self.assertEqual(completed_phase, "work")
        self.assertEqual(new_state["phase"], "break")
        self.assertEqual(new_state["remaining_seconds"], 5 * 60)

    def test_tick_keeps_running_true_through_transition(self):
        cfg = config()
        state = {
            "phase": "break", "running": True, "remaining_seconds": 1,
            "cycle": 1, "cycles_until_long_break": 4, "timestamp": 0,
        }
        new_state, transitioned, _ = tick(state, cfg)
        self.assertTrue(transitioned)
        self.assertTrue(new_state["running"])


class ValidateConfigUpdatesTests(unittest.TestCase):
    def test_accepts_valid_updates_and_coerces_to_int(self):
        cleaned = validate_config_updates({"work_minutes": "30", "cycles_until_long_break": "3"})
        self.assertEqual(cleaned, {"work_minutes": 30, "cycles_until_long_break": 3})

    def test_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            validate_config_updates({"not_a_real_key": "5"})

    def test_rejects_non_integer_value(self):
        with self.assertRaises(ValueError):
            validate_config_updates({"work_minutes": "abc"})

    def test_rejects_out_of_range_minutes(self):
        with self.assertRaises(ValueError):
            validate_config_updates({"work_minutes": "0"})
        with self.assertRaises(ValueError):
            validate_config_updates({"break_minutes": "500"})

    def test_rejects_out_of_range_cycles(self):
        with self.assertRaises(ValueError):
            validate_config_updates({"cycles_until_long_break": "0"})
        with self.assertRaises(ValueError):
            validate_config_updates({"cycles_until_long_break": "50"})


class SanitizeConfigTests(unittest.TestCase):
    def test_non_dict_falls_back_to_defaults(self):
        self.assertEqual(sanitize_config(None), DEFAULT_CONFIG)
        self.assertEqual(sanitize_config("25"), DEFAULT_CONFIG)

    def test_out_of_range_value_falls_back_per_key(self):
        cleaned = sanitize_config({"work_minutes": 9999, "break_minutes": 7})
        self.assertEqual(cleaned["work_minutes"], DEFAULT_CONFIG["work_minutes"])
        self.assertEqual(cleaned["break_minutes"], 7)

    def test_unknown_keys_are_dropped(self):
        cleaned = sanitize_config({"work_minutes": 30, "evil": "rm -rf"})
        self.assertNotIn("evil", cleaned)
        self.assertEqual(cleaned["work_minutes"], 30)


class SanitizeStateTests(unittest.TestCase):
    def valid(self, **overrides):
        state = {
            "phase": "work", "running": True, "remaining_seconds": 90,
            "cycle": 2, "cycles_until_long_break": 4, "timestamp": 1.0,
        }
        state.update(overrides)
        return state

    def test_accepts_a_well_formed_state(self):
        cleaned = sanitize_state(self.valid(), DEFAULT_CONFIG)
        self.assertEqual(cleaned["phase"], "work")
        self.assertEqual(cleaned["remaining_seconds"], 90)
        self.assertEqual(cleaned["cycle"], 2)

    def test_rejects_unknown_phase_and_non_dicts(self):
        self.assertIsNone(sanitize_state(self.valid(phase="pwned"), DEFAULT_CONFIG))
        self.assertIsNone(sanitize_state(["work"], DEFAULT_CONFIG))
        self.assertIsNone(sanitize_state(None, DEFAULT_CONFIG))

    def test_rejects_out_of_range_remaining_seconds(self):
        self.assertIsNone(sanitize_state(self.valid(remaining_seconds=-1), DEFAULT_CONFIG))
        self.assertIsNone(sanitize_state(self.valid(remaining_seconds=10 ** 9), DEFAULT_CONFIG))
        self.assertIsNone(sanitize_state(self.valid(remaining_seconds="60"), DEFAULT_CONFIG))

    def test_out_of_range_cycle_fields_fall_back_instead_of_rejecting(self):
        cleaned = sanitize_state(
            self.valid(cycle=0, cycles_until_long_break=999), DEFAULT_CONFIG)
        self.assertEqual(cleaned["cycle"], 1)
        self.assertEqual(cleaned["cycles_until_long_break"],
                         DEFAULT_CONFIG["cycles_until_long_break"])

    def test_running_is_coerced_to_a_real_bool(self):
        cleaned = sanitize_state(self.valid(running="yes"), DEFAULT_CONFIG)
        self.assertIs(cleaned["running"], True)


class PublicSnapshotTests(unittest.TestCase):
    def test_snapshot_exposes_only_the_agreed_fields(self):
        state = fresh_work_state(DEFAULT_CONFIG, running=True)
        state["secret"] = "should not reach the UI"
        snapshot = public_snapshot(state, DEFAULT_CONFIG)
        self.assertEqual(
            sorted(snapshot["state"]),
            ["cycle", "cycles_until_long_break", "phase", "remaining_seconds", "running"],
        )
        self.assertEqual(sorted(snapshot["config"]), sorted(DEFAULT_CONFIG))

    def test_snapshot_is_one_stable_json_line(self):
        state = fresh_work_state(DEFAULT_CONFIG, running=False)
        first = json.dumps(public_snapshot(state, DEFAULT_CONFIG), sort_keys=True)
        second = json.dumps(public_snapshot(state, DEFAULT_CONFIG), sort_keys=True)
        self.assertEqual(first, second)  # no timestamp: idle does not churn
        self.assertNotIn("\n", first)


class StateDirTests(unittest.TestCase):
    """The private directory is the whole of the filesystem defence, so the
    refusals it is supposed to make are asserted rather than assumed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.dir_path = os.path.join(self.tmp, "state")
        self.store = StateDir(self.dir_path)
        self.addCleanup(self.store.close)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_directory_is_created_owner_only(self):
        mode = stat.S_IMODE(os.stat(self.dir_path).st_mode)
        self.assertEqual(mode, 0o700)

    def test_pre_existing_loose_permissions_are_tightened(self):
        os.chmod(self.dir_path, 0o755)
        other = StateDir(self.dir_path)
        self.addCleanup(other.close)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir_path).st_mode), 0o700)

    def test_symlinked_state_directory_is_refused(self):
        link = os.path.join(self.tmp, "link")
        os.symlink(self.dir_path, link)
        with self.assertRaises(StateDirError):
            StateDir(link)

    def test_round_trips_json_through_an_atomic_replace(self):
        self.store.write_json(STATE_NAME, {"phase": "work"})
        self.assertEqual(self.store.read_json(STATE_NAME), {"phase": "work"})
        # The temporary is gone, and the real file is owner-only.
        leftovers = [n for n in os.listdir(self.dir_path) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        mode = stat.S_IMODE(os.stat(os.path.join(self.dir_path, STATE_NAME)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_symlinked_state_file_is_refused_not_followed(self):
        target = os.path.join(self.tmp, "elsewhere.json")
        with open(target, "w") as handle:
            json.dump({"phase": "work", "remaining_seconds": 1}, handle)
        os.symlink(target, os.path.join(self.dir_path, STATE_NAME))
        with self.assertRaises(OSError):
            self.store.read_text(STATE_NAME)
        self.assertIsNone(self.store.read_json(STATE_NAME))

    def test_writing_over_a_symlink_replaces_the_link_not_its_target(self):
        target = os.path.join(self.tmp, "elsewhere.json")
        with open(target, "w") as handle:
            handle.write("original")
        os.symlink(target, os.path.join(self.dir_path, CONFIG_NAME))
        self.store.write_json(CONFIG_NAME, {"work_minutes": 30})
        with open(target) as handle:
            self.assertEqual(handle.read(), "original")
        self.assertFalse(os.path.islink(os.path.join(self.dir_path, CONFIG_NAME)))

    def test_fifo_is_refused_instead_of_blocking(self):
        os.mkfifo(os.path.join(self.dir_path, STATE_NAME))
        with self.assertRaises(StateDirError):
            self.store.read_text(STATE_NAME)
        self.assertIsNone(self.store.read_json(STATE_NAME))

    def test_oversized_file_is_refused(self):
        with open(os.path.join(self.dir_path, STATE_NAME), "w") as handle:
            handle.write("x" * (MAX_FILE_BYTES + 1))
        with self.assertRaises(StateDirError):
            self.store.read_text(STATE_NAME)

    def test_refuses_to_write_more_than_the_bound(self):
        with self.assertRaises(StateDirError):
            self.store.write_json(STATE_NAME, {"blob": "x" * (MAX_FILE_BYTES + 1)})

    def test_state_lock_is_exclusive_across_descriptors(self):
        with state_lock(self.store):
            other = StateDir(self.dir_path)
            self.addCleanup(other.close)
            with self.assertRaises(StateDirError):
                with state_lock(other, timeout=0.05):
                    pass

    def test_daemon_lock_reports_liveness_without_trusting_a_pid(self):
        self.assertFalse(daemon_is_running(self.store))
        fd = self.store.open_lock("daemon.lock")
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(daemon_is_running(self.store))
        finally:
            os.close(fd)
        self.assertFalse(daemon_is_running(self.store))


class ChildIdentityTests(unittest.TestCase):
    def test_child_environment_is_closed_to_inherited_variables(self):
        os.environ["POMODORO_TEST_LEAK"] = "leaked"
        self.addCleanup(os.environ.pop, "POMODORO_TEST_LEAK", None)
        env = child_environment()
        self.assertNotIn("POMODORO_TEST_LEAK", env)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertIn("HOME", env)

    def test_trusted_program_rejects_a_user_owned_impostor(self):
        with tempfile.TemporaryDirectory() as tmp:
            impostor = os.path.join(tmp, "notify-send")
            with open(impostor, "w") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(impostor, 0o755)
            self.assertIsNone(trusted_program((impostor,)))

    def test_trusted_program_accepts_a_real_root_owned_binary(self):
        # /bin/sh exists on every system this plugin runs on; if it were ever
        # not root-owned the whole trust model would already be void.
        self.assertEqual(trusted_program(("/bin/sh",)), "/bin/sh")


class BarGlyphTests(unittest.TestCase):
    """The idle widget is a single private-use codepoint, so losing it fails
    silently: the bar renders an empty string, nothing logs an error, and the
    widget just disappears. Assert the byte is there."""

    IDLE_GLYPH = "\ue001"  # Pomicons pom-pomodoro_done

    def widget_source(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "BarWidget.qml")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def declared_glyph(self):
        match = re.search(r'idleGlyph:\s*"([^"]*)"', self.widget_source())
        self.assertIsNotNone(match, "no idleGlyph declaration in BarWidget.qml")
        return match.group(1)

    def test_idle_glyph_is_the_pomicons_tomato(self):
        self.assertEqual(
            [hex(ord(c)) for c in self.declared_glyph()],
            [hex(ord(self.IDLE_GLYPH))],
            "BarWidget.qml idleGlyph is not U+E001; the stopped bar widget "
            "will render blank",
        )


if __name__ == "__main__":
    unittest.main()

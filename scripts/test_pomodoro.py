#!/usr/bin/env python3
"""Unit tests for the pure phase-transition logic in pomodoro.py.

Runs with the stdlib only: python3 scripts/test_pomodoro.py
"""
import unittest

from pomodoro import (
    DEFAULT_CONFIG,
    advance_phase,
    fresh_work_state,
    initial_state,
    tick,
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


if __name__ == "__main__":
    unittest.main()

"""Driving a child process without losing track of it.

These run real subprocesses — this interpreter, given a one-line script — because
the things worth testing here are the ones that only go wrong against a real
pipe: a prompt too large for the kernel buffer, a child that never exits, a
child killed part-way.
"""

from __future__ import annotations

import sys
import threading
import unittest
from unittest import mock

from reviewer import model

#: The prompt every one of these sends, unless it is testing the size of it.
PROMPT = '{"findings": []}'


def child(script: str, *, timeout: float = 30.0, **cfg) -> dict:
    """A provider config that runs one line of Python instead of a model CLI."""
    return {
        "type": "command",
        "command": sys.executable,
        "extra_args": ["-c", script],
        "timeout_seconds": timeout,
        **cfg,
    }


#: Reads the whole prompt and prints a JSON object back, which is the shape
#: every adapter expects and the smallest thing model.run will accept.
ECHO = (
    "import sys; "
    "data = sys.stdin.read(); "
    'print(\'{"length": %d}\' % len(data))'
)


class RunningACall(unittest.TestCase):
    def test_a_prompt_larger_than_a_pipe_buffer_does_not_deadlock(self):
        # The reason the prompt is written on its own thread. A pipe holds
        # 64KB; a review bundle is bigger, and writing it inline would block
        # against a child that has not started reading yet — forever, because
        # nothing else is draining its output either.
        big = "x" * 400_000
        result = model.run(child(ECHO), system_prompt="", user_prompt=big)
        # At least, rather than exactly: this provider folds the system prompt
        # into the message, so what reaches the child is the prompt plus its
        # wrapper. What matters is that the whole of it arrived.
        self.assertGreaterEqual(result.payload["length"], len(big))

    def test_usage_and_duration_come_back(self):
        result = model.run(child(ECHO), system_prompt="", user_prompt=PROMPT)
        self.assertGreaterEqual(result.duration_seconds, 0.0)
        self.assertEqual(result.provider, "command")

    def test_stderr_is_quoted_when_the_child_fails(self):
        script = "import sys; sys.stderr.write('it went wrong'); sys.exit(3)"
        with self.assertRaises(model.ModelError) as caught:
            model.run(child(script), system_prompt="", user_prompt=PROMPT)
        self.assertIn("it went wrong", str(caught.exception))
        self.assertIn("exited 3", str(caught.exception))


class GivingUp(unittest.TestCase):
    def test_a_child_that_never_finishes_is_stopped(self):
        script = "import time; time.sleep(60)"
        with mock.patch.object(model, "BEAT_SECONDS", 0.05):
            with self.assertRaises(model.ModelError) as caught:
                model.run(
                    child(script, timeout=0.3), system_prompt="", user_prompt=PROMPT
                )
        self.assertIn("gave up", str(caught.exception))

    def test_giving_up_says_how_much_of_the_wait_was_sleep(self):
        # The number that explains an alarming one. A call that ran for an hour
        # of wall clock and three minutes of awake time is not the same event.
        script = "import time; time.sleep(60)"
        with mock.patch.object(model, "BEAT_SECONDS", 0.05):
            with self.assertRaises(model.ModelError) as caught:
                model.run(
                    child(script, timeout=0.3), system_prompt="", user_prompt=PROMPT
                )
        self.assertIn("asleep", str(caught.exception))


class ReportingProgress(unittest.TestCase):
    def test_a_running_call_checks_in(self):
        script = "import time; time.sleep(0.4); print('{}')"
        seen = []
        with mock.patch.object(model, "BEAT_SECONDS", 0.05):
            model.run(
                child(script),
                system_prompt="",
                user_prompt=PROMPT,
                on_progress=seen.append,
            )
        self.assertTrue(seen, "a call lasting several beats reported none")
        self.assertGreaterEqual(seen[-1].elapsed, 0.0)
        self.assertGreaterEqual(seen[-1].silent_for, 0.0)

    def test_a_heartbeat_that_raises_does_not_lose_the_review(self):
        # A heartbeat is bookkeeping. Losing a paid-for review because the
        # database was busy would be a bad trade.
        def explode(_progress):
            raise RuntimeError("no")

        script = "import time; time.sleep(0.3); print('{\"ok\": 1}')"
        with mock.patch.object(model, "BEAT_SECONDS", 0.05):
            result = model.run(
                child(script),
                system_prompt="",
                user_prompt=PROMPT,
                on_progress=explode,
            )
        self.assertEqual(result.payload, {"ok": 1})

    def test_silence_is_measured_from_the_last_line_not_the_start(self):
        # A call that has been running for a while and just spoke is working.
        # Telling that apart from one that has said nothing is the whole point.
        script = (
            "import sys, time\n"
            "for _ in range(6):\n"
            "    print('tick'); sys.stdout.flush(); time.sleep(0.05)\n"
            "print('{}')"
        )
        seen = []
        with mock.patch.object(model, "BEAT_SECONDS", 0.02):
            model.run(
                child(script),
                system_prompt="",
                user_prompt=PROMPT,
                on_progress=seen.append,
            )
        self.assertTrue(seen)
        # Nothing went quiet for anywhere near the length of the call.
        self.assertLess(max(progress.silent_for for progress in seen), 0.5)


class Cancelling(unittest.TestCase):
    def tearDown(self):
        model._cancelled.clear()
        model._cancelled_labels.clear()

    def test_a_labelled_call_can_be_stopped_by_name(self):
        script = "import time; time.sleep(60)"
        failure = {}

        def call():
            try:
                model.run(
                    child(script),
                    system_prompt="",
                    user_prompt=PROMPT,
                    label="acme/widgets#7",
                )
            except model.ModelError as exc:
                failure["error"] = str(exc)

        worker = threading.Thread(target=call)
        worker.start()
        # Wait for the child to actually exist before asking for it by name.
        for _ in range(200):
            if model.live_count():
                break
            threading.Event().wait(0.02)
        self.assertEqual(model.cancel("acme/widgets#7"), 1)
        worker.join(timeout=20)
        self.assertFalse(worker.is_alive())
        # Phrased as a cancellation, not as the crash a negative exit status
        # would otherwise be reported as.
        self.assertIn("cancelled", failure.get("error", ""))

    def test_cancelling_something_that_is_not_running_stops_nothing(self):
        self.assertEqual(model.cancel("acme/widgets#999"), 0)

    def test_a_cancel_that_lost_the_race_leaves_no_mark_behind(self):
        # Cancelling a call that was about to exit anyway still marks the label.
        # If that mark survived, the next call on the same pull request would
        # report an unrelated failure as something the user had asked for.
        model._cancelled_labels.add("acme/widgets#7")
        script = "print('{\"ok\": 1}')"
        model.run(
            child(script),
            system_prompt="",
            user_prompt=PROMPT,
            label="acme/widgets#7",
        )
        self.assertNotIn("acme/widgets#7", model._cancelled_labels)

    def test_the_cancelled_mark_is_cleared_once_read(self):
        # The label is a pull request, and the next tick will use it again for
        # a call nobody asked to stop.
        model._cancelled_labels.add("acme/widgets#7")
        self.assertTrue(model.was_cancelled("acme/widgets#7"))
        self.assertFalse(model.was_cancelled("acme/widgets#7"))


class ExtractingJson(unittest.TestCase):
    def test_prose_either_side_of_the_object_is_survivable(self):
        # A review is too expensive to throw away over a stray "Here you go:".
        payload = model.extract_json('Here you go:\n{"findings": []}\nHope that helps')
        self.assertEqual(payload, {"findings": []})

    def test_a_fenced_block_is_unwrapped(self):
        payload = model.extract_json('```json\n{"findings": []}\n```')
        self.assertEqual(payload, {"findings": []})

    def test_nothing_at_all_is_an_error(self):
        with self.assertRaises(model.ModelError):
            model.extract_json("   ")


if __name__ == "__main__":
    unittest.main()

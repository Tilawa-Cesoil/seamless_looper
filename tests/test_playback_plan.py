from __future__ import annotations

import threading
import unittest

import numpy as np

from seamless_looper.audio_io import LoadedTrack
from seamless_looper.playback import Phase, PlaybackPlan


def make_track(intro_frames=5, loop_frames=7, outro_frames=4, channels=2) -> LoadedTrack:
    counter = 0

    def block(length: int, start: float) -> np.ndarray:
        nonlocal counter
        data = np.empty((length, channels), dtype=np.float32)
        for frame in range(length):
            data[frame, :] = start + frame * 0.01
        counter += 1
        return data

    return LoadedTrack(
        meta={},
        intro=block(intro_frames, 0.10),
        loop=block(loop_frames, 0.40),
        outro=block(outro_frames, 0.80),
        sample_rate=44100,
        channels=channels,
    )


def render_all(plan: PlaybackPlan, block: int) -> np.ndarray:
    chunks = []
    while plan.phase is not Phase.DONE:
        data, produced = plan.render(block)
        if produced:
            chunks.append(data[:produced])
        if produced == 0:
            break
    return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, plan.track.channels), dtype=np.float32)


def expected_finite(track: LoadedTrack, repeats: int) -> np.ndarray:
    return np.concatenate([track.intro] + [track.loop] * repeats + [track.outro], axis=0)


class PlaybackPlanTests(unittest.TestCase):
    def test_finite_loop_concat_is_sample_exact(self) -> None:
        track = make_track()
        for block in (1, 2, 3, 5, 7, 11, 32, 128):
            with self.subTest(block=block):
                plan = PlaybackPlan(track, 3)
                audio = render_all(plan, block)
                np.testing.assert_array_equal(audio, expected_finite(track, 3))
                self.assertEqual(plan.loops_completed, 3)

    def test_segments_shorter_and_longer_than_block(self) -> None:
        for intro, loop, outro in ((1, 1, 1), (2, 3, 5), (17, 4, 9), (600, 700, 200)):
            with self.subTest(intro=intro, loop=loop, outro=outro):
                track = make_track(intro, loop, outro)
                plan = PlaybackPlan(track, 2)
                audio = render_all(plan, 13)
                np.testing.assert_array_equal(audio, expected_finite(track, 2))
                self.assertEqual(plan.loops_completed, 2)

    def test_no_silent_padding_between_sections(self) -> None:
        track = make_track(4, 6, 3)
        plan = PlaybackPlan(track, 2)
        audio = render_all(plan, 5)
        self.assertFalse(np.any(np.all(audio == 0, axis=1)))

    def test_end_during_intro_skips_all_loops(self) -> None:
        track = make_track(8, 5, 4)
        event = threading.Event()
        plan = PlaybackPlan(track, 10, end_event=event)
        first, _ = plan.render(3)
        event.set()
        rest = render_all(plan, 3)
        audio = np.concatenate([first, rest])
        np.testing.assert_array_equal(audio, np.concatenate([track.intro, track.outro]))
        self.assertEqual(plan.loops_completed, 0)

    def test_end_during_loop_finishes_current_pass(self) -> None:
        track = make_track(6, 10, 5)
        event = threading.Event()
        plan = PlaybackPlan(track, None, end_event=event)
        chunks = []
        # Consume intro plus part of the first loop.
        for _ in range(3):
            data, _ = plan.render(4)
            chunks.append(data)
        self.assertEqual(plan.phase, Phase.LOOP)
        event.set()
        chunks.append(render_all(plan, 4))
        audio = np.concatenate(chunks)
        expected = np.concatenate([track.intro, track.loop, track.outro])
        np.testing.assert_array_equal(audio, expected)
        self.assertEqual(plan.loops_completed, 1)

    def test_end_during_outro_is_ignored(self) -> None:
        track = make_track(2, 3, 6)
        event = threading.Event()
        plan = PlaybackPlan(track, 1, end_event=event)
        chunks = []
        while plan.phase is not Phase.OUTRO:
            data, _ = plan.render(2)
            chunks.append(data)
        event.set()
        chunks.append(render_all(plan, 2))
        audio = np.concatenate(chunks)
        np.testing.assert_array_equal(audio, expected_finite(track, 1))

    def test_infinite_loop_can_stop_after_two_passes(self) -> None:
        track = make_track(2, 4, 3)
        event = threading.Event()
        plan = PlaybackPlan(track, None, end_event=event)
        chunks = []
        # Read exactly intro + two complete loops.
        needed = len(track.intro) + 2 * len(track.loop)
        while sum(len(chunk) for chunk in chunks) < needed:
            data, _ = plan.render(1)
            chunks.append(data)
        self.assertEqual(plan.loops_completed, 2)
        self.assertEqual(plan.phase, Phase.LOOP)
        event.set()
        chunks.append(render_all(plan, 1))
        audio = np.concatenate(chunks)
        expected = np.concatenate([track.intro, track.loop, track.loop, track.outro])
        np.testing.assert_array_equal(audio, expected)
        self.assertEqual(plan.loops_completed, 2)

    def test_zero_repeats_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PlaybackPlan(make_track(), 0)


if __name__ == "__main__":
    unittest.main()

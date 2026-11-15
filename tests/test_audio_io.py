from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import soundfile as sf

from seamless_looper import audio_io

WORKSPACE_TMP = Path(__file__).resolve().parent.parent / ".test-tmp"
WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)


@contextmanager
def workspace_temp():
    """A temp dir inside the workspace.

    Windows' tempfile.mkdtemp() creates a mode-700 directory that the DSH
    workspace sandbox cannot write into.  A plainly created subdirectory does
    inherit the workspace ACEs and can be cleaned up normally.
    """
    folder = WORKSPACE_TMP / f"t-{uuid.uuid4().hex[:10]}"
    folder.mkdir()
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def tone(frames=44100, channels=2, sample_rate=44100, dtype="float32") -> np.ndarray:
    t = np.arange(frames, dtype=np.float64) / sample_rate
    left = 0.37 * np.sin(2 * np.pi * 220 * t)
    right = 0.23 * np.sin(2 * np.pi * 330 * t + 0.3)
    data = np.stack([left, right], axis=1)[:, :channels]
    return data.astype(dtype)


def write_wav(path: Path, subtype: str = "PCM_16", frames=44100) -> None:
    sf.write(str(path), tone(frames=frames), 44100, format="WAV", subtype=subtype)


class FrameGridTests(unittest.TestCase):
    def test_sample_grid_never_allocates_a_full_arange(self) -> None:
        grid = audio_io.FrameGrid(7_574_504, 1, "1 采样帧")
        self.assertEqual(grid.minimum_cut, 1)
        self.assertEqual(grid.maximum_cut, 7_574_503)
        self.assertEqual(grid.snap(0, low=0, high=100), 1)
        self.assertEqual(grid.snap(7_574_504, low=0, high=7_574_504), 7_574_503)
        self.assertEqual(grid.nudge(100, -1), 99)
        self.assertEqual(grid.nudge(100, 1), 101)

    def test_fixed_packet_grid_snaps_and_nudges(self) -> None:
        grid = audio_io.FrameGrid(100_000, 1152, "1152 / frame")
        self.assertEqual(grid.snap(5000), 4608)
        self.assertEqual(grid.nudge(5000, 1), 5760)
        self.assertEqual(grid.nudge(5000, -1), 4608)
        self.assertEqual(grid.minimum_cut, 1152)
        self.assertEqual(grid.maximum_cut, 99_072)

    def test_variable_packet_boundaries(self) -> None:
        boundaries = np.array([0, 960, 1920, 2880, 5000], dtype=np.int64)
        grid = audio_io.FrameGrid(5000, 960, "Opus 包边界", boundaries=boundaries)
        self.assertEqual(grid.snap(1500), 1920)
        self.assertEqual(grid.nudge(1430, 1), 1920)
        self.assertEqual(grid.nudge(1430, -1), 960)
        self.assertEqual(grid.snap_cut2(999_999, 960), 2880)


class AudioIoTests(unittest.TestCase):
    def test_probe_and_lossless_flac_roundtrip(self) -> None:
        with workspace_temp() as root:
            source = root / "source.flac"
            data = tone(22050)
            sf.write(str(source), data, 44100, format="FLAC", subtype="PCM_16")
            info = audio_io.probe_audio(source)
            self.assertEqual(info.sample_rate, 44100)
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.bit_depth, 16)
            self.assertEqual(info.codec, "flac")
            self.assertEqual(info.output.container, "FLAC")

            decoded = audio_io.decode_audio(source)
            self.assertEqual(decoded.pcm.shape, (22050, 2))
            cut1, cut2 = 3000, 17000
            library = root / "library"
            saved = audio_io.save_track(
                decoded,
                cut1,
                cut2,
                library_dir=library,
                title="Test",
                artist="Unit",
            )
            self.assertTrue((saved.directory / "meta.json").exists())
            for name, frames in (("intro", cut1), ("loop", cut2 - cut1), ("outro", 22050 - cut2)):
                segment = saved.directory / f"{name}.flac"
                seg_info = sf.info(str(segment))
                self.assertEqual(seg_info.samplerate, 44100)
                self.assertEqual(seg_info.channels, 2)
                self.assertEqual(seg_info.subtype, "PCM_16")
                self.assertEqual(seg_info.frames, frames)
            loaded = audio_io.load_track(audio_io.scan_library(library)[0])
            joined = np.concatenate([loaded.intro, loaded.loop, loaded.outro])
            np.testing.assert_array_equal(joined, decoded.pcm)

    def test_24_bit_wav_stays_24_bit(self) -> None:
        with workspace_temp() as root:
            source = root / "source.wav"
            write_wav(source, "PCM_24", frames=12000)
            decoded = audio_io.decode_audio(source)
            self.assertEqual(decoded.info.bit_depth, 24)
            saved = audio_io.save_track(
                decoded,
                2000,
                9000,
                library_dir=root / "lib",
                title="24 bit",
                artist="Test",
            )
            intro = sf.info(str(saved.directory / "intro.wav"))
            self.assertEqual(intro.subtype, "PCM_24")
            self.assertEqual(intro.samplerate, 44100)
            self.assertEqual(intro.channels, 2)

    def test_32_bit_float_wav_stays_float(self) -> None:
        with workspace_temp() as root:
            source = root / "source.wav"
            write_wav(source, "FLOAT", frames=8000)
            decoded = audio_io.decode_audio(source)
            saved = audio_io.save_track(
                decoded,
                1000,
                7000,
                library_dir=root / "lib",
                title="float",
                artist="Test",
            )
            info = sf.info(str(saved.directory / "intro.wav"))
            self.assertEqual(info.subtype, "FLOAT")
            self.assertEqual(decoded.pcm.dtype, np.float32)

    def test_32_bit_integer_wav_keeps_32_bits(self) -> None:
        with workspace_temp() as root:
            source = root / "source32.wav"
            write_wav(source, "PCM_32", frames=9000)
            decoded = audio_io.decode_audio(source)
            self.assertEqual(decoded.info.bit_depth, 32)
            self.assertEqual(decoded.pcm.dtype, np.float64)
            saved = audio_io.save_track(
                decoded,
                1500,
                7500,
                library_dir=root / "lib",
                title="32 bit",
                artist="Test",
            )
            info = sf.info(str(saved.directory / "intro.wav"))
            self.assertEqual(info.subtype, "PCM_32")
            segment, _ = sf.read(str(saved.directory / "intro.wav"), dtype="float64", always_2d=True)
            np.testing.assert_array_equal(segment, decoded.pcm[:1500])

    def test_invalid_cut_order_is_rejected(self) -> None:
        with workspace_temp() as root:
            source = root / "source.wav"
            write_wav(source, "PCM_16", frames=1000)
            decoded = audio_io.decode_audio(source)
            with self.assertRaises(audio_io.AudioError):
                audio_io.save_track(
                    decoded, 500, 100, library_dir=root / "lib", title="x", artist="y"
                )
            with self.assertRaises(audio_io.AudioError):
                audio_io.save_track(
                    decoded, 0, 500, library_dir=root / "lib", title="x", artist="y"
                )

    def test_unsupported_extension_is_explicit(self) -> None:
        with workspace_temp() as root:
            path = root / "song.xyz"
            path.write_bytes(b"not audio")
            with self.assertRaises(audio_io.UnsupportedAudioError):
                audio_io.probe_audio(path)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg missing")
    def test_compressed_formats_probe_and_frame_grid(self) -> None:
        with workspace_temp() as root:
            source_wav = root / "source.wav"
            write_wav(source_wav, "PCM_16", frames=44100)
            cases = [
                ("mp3", ["-codec:a", "libmp3lame", "-b:a", "192k"], 1152, "MP3"),
                ("m4a", ["-codec:a", "aac", "-b:a", "192k"], 1024, "AAC"),
                ("ogg", ["-codec:a", "libopus", "-b:a", "128k"], 960, "Opus"),
                ("ogg", ["-codec:a", "libvorbis", "-q:a", "5"], 1024, "Vorbis"),
            ]
            for ext, options, expected_step, label in cases:
                with self.subTest(label=label):
                    target = root / f"test_{label}.{ext}"
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source_wav),
                        *options,
                        str(target),
                    ]
                    subprocess.run(cmd, check=True, capture_output=True)
                    info = audio_io.probe_audio(target)
                    if label == "Opus":
                        self.assertEqual(info.sample_rate, 48000)
                    else:
                        self.assertEqual(info.sample_rate, 44100)
                    if label != "Vorbis":
                        self.assertEqual(info.frame_step, expected_step)
                    self.assertTrue(info.frame_label)
                    self.assertIn(label, info.frame_label)
                    decoded = audio_io.decode_audio(target)
                    self.assertGreater(decoded.info.frames, 30000)
                    saved = audio_io.save_track(
                        decoded,
                        decoded.info.frame_step,
                        decoded.info.frames - decoded.info.frame_step,
                        library_dir=root / f"lib_{label}",
                        title=label,
                        artist="Test",
                    )
                    self.assertEqual(sf.info(str(saved.directory / "loop.flac")).subtype, "PCM_24")

            # MPEG-2 Layer III (low sample rate) uses 576 samples/frame.
            low_rate = root / "low_rate.mp3"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source_wav),
                    "-ar",
                    "22050",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "96k",
                    str(low_rate),
                ],
                check=True,
                capture_output=True,
            )
            low_info = audio_io.probe_audio(low_rate)
            self.assertEqual(low_info.sample_rate, 22050)
            self.assertEqual(low_info.frame_step, 576)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg missing")
    def test_alac_m4a_is_decoded_and_rehomed_to_flac(self) -> None:
        with workspace_temp() as root:
            source_wav = root / "source.wav"
            target = root / "alac.m4a"
            write_wav(source_wav, "PCM_16", frames=32000)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source_wav),
                    "-c:a",
                    "alac",
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
            info = audio_io.probe_audio(target)
            self.assertEqual(info.codec, "alac")
            self.assertEqual(info.frame_step, 1)
            self.assertEqual(info.output.container, "FLAC")
            decoded = audio_io.decode_audio(target)
            saved = audio_io.save_track(
                decoded,
                4000,
                26000,
                library_dir=root / "lib_alac",
                title="ALAC",
                artist="Test",
            )
            self.assertEqual(sf.info(str(saved.directory / "loop.flac")).subtype, "PCM_16")

    def test_meta_json_has_expected_shape(self) -> None:
        with workspace_temp() as root:
            source = root / "source.wav"
            write_wav(source, "PCM_16", frames=9000)
            saved = audio_io.save_cut_track_from_file(
                source,
                2000,
                7000,
                library_dir=root / "lib",
                title="Meta",
                artist="Artist",
            )
            meta = json.loads((saved.directory / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["cut"]["start_sample"], 2000)
            self.assertEqual(meta["cut"]["end_sample"], 7000)
            self.assertEqual(meta["segments"]["loop"]["frames"], 5000)
            self.assertEqual(meta["playback"]["sample_rate"], 44100)
            self.assertEqual(meta["playback"]["channels"], 2)
            self.assertIn("title", meta)
            self.assertIn("artist", meta)


if __name__ == "__main__":
    unittest.main()

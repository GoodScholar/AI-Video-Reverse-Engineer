"""Bounded, local source-audio peak extraction; no project state mutation."""
import json
import subprocess
import sys
from array import array
from collections import OrderedDict
from threading import Lock, BoundedSemaphore


class WaveformCache:
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()
        self.decoders = BoundedSemaphore(2)

    def get(self, path, ffmpeg_path, ffprobe_path):
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key]
        if not self.decoders.acquire(blocking=False):
            raise ValueError("波形读取繁忙，请稍后重新加载波形。")
        try:
            result = extract_waveform(path, ffmpeg_path, ffprobe_path)
            after = path.stat()
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != key[1:]:
                raise ValueError("素材已变化，请重新读取波形。")
            with self.lock:
                self.entries[key] = result
                while len(self.entries) > 32:
                    self.entries.popitem(last=False)
            return result
        finally:
            self.decoders.release()


def extract_waveform(path, ffmpeg_path, ffprobe_path):
    try:
        probe = subprocess.run([ffprobe_path, '-v', 'error', '-show_entries', 'stream=codec_type,channels', '-of', 'json', str(path)], capture_output=True, timeout=15, check=True)
        streams = json.loads(probe.stdout).get('streams', [])
        if not any(stream.get('codec_type') == 'audio' for stream in streams):
            if not streams:
                raise ValueError("素材不含可读取的媒体流。")
            return {'status': 'no_audio', 'peaks': [], 'duration': 0, 'peaksPerSecond': 100}
        audio = next(stream for stream in streams if stream.get("codec_type") == "audio")
        channels = 1 if audio.get("channels") == 1 else 2
        # Keep both channels so opposite-phase stereo does not disappear in a mono mix.
        decoded = subprocess.run([ffmpeg_path, '-v', 'error', '-nostdin', '-threads', '1', '-i', str(path), '-map', '0:a:0',
            '-af', 'aresample=8000:async=1:first_pts=0', '-ac', str(channels), '-t', '300.01', '-f', 's16le', 'pipe:1'], capture_output=True, timeout=45, check=True)
        samples = array('h', decoded.stdout)
        if sys.byteorder != 'little':
            samples.byteswap()
        if not samples:
            raise ValueError("音轨中没有可读取的采样。")
        if len(samples) > 300 * 8000 * channels:
            raise ValueError("波形暂支持 300 秒以内的素材，请先裁切；仍可继续剪辑。")
        bucket = 80 * channels
        peaks = [round(max(abs(value) for value in samples[index:index + bucket]) / 32768, 4) for index in range(0, len(samples), bucket)]
        return {'status': 'ready', 'duration': len(samples) / (8000 * channels), 'peaksPerSecond': 100, 'peaks': peaks}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ValueError("无法读取音频波形，请检查素材和本地 FFmpeg 后重试。") from error

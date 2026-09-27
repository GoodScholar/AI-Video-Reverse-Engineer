"""Convert saved caption cues to and from the local subtitle worker's SRT format."""
from __future__ import annotations

import re
from pathlib import Path


_TIMING = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def _seconds(parts: tuple[str, ...]) -> float:
    hours, minutes, seconds, milliseconds = map(int, parts)
    if minutes >= 60 or seconds >= 60:
        raise ValueError("识别字幕时间码无效")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt(path: Path) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()):
        if not block:
            continue
        lines = block.splitlines()
        timing_index = next((index for index, line in enumerate(lines[:2]) if _TIMING.fullmatch(line.strip())), None)
        timing = _TIMING.fullmatch(lines[timing_index].strip()) if timing_index is not None else None
        if timing is None:
            raise ValueError("识别字幕格式无效")
        text = " ".join(line.strip() for line in lines[timing_index + 1:] if line.strip())
        cues.append({"id": f"line-{len(cues) + 1}", "start": _seconds(timing.groups()[:4]),
                     "end": _seconds(timing.groups()[4:]), "text": text})
    return cues


def _timestamp(seconds: float) -> str:
    total = milliseconds(seconds)
    hours, total = divmod(total, 3_600_000)
    minutes, total = divmod(total, 60_000)
    whole, millis = divmod(total, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def milliseconds(seconds: float) -> int:
    return int(seconds * 1000 + 0.5)


def write_srt(path: Path, cues: list[dict]) -> None:
    path.write_text("\n\n".join(
        f"{index}\n{_timestamp(cue['start'])} --> {_timestamp(cue['end'])}\n{cue['text'].replace(chr(13), ' ').replace(chr(10), ' ')}"
        for index, cue in enumerate(cues, start=1)
    ) + "\n", encoding="utf-8")


def caption_images(srt_path: Path, width: int, height: int) -> list[tuple[Path, float, float]]:
    from PIL import Image, ImageDraw, ImageFont

    fonts = [Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
             Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
             Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
             Path("C:/Windows/Fonts/msyh.ttc")]
    font_path = next((path for path in fonts if path.is_file()), None)
    if font_path is None:
        raise ValueError("没有可用的字幕字体。")
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    side_margin = min(20, max(8, width // 16))
    text_margin = min(50, max(12, width // 12))
    max_text_width = max(16, width - text_margin * 2)
    bottom_margin = max(32, round(height * 0.05))
    max_caption_height = max(58, min(max(58, height - bottom_margin * 2), round(height * 0.45)))
    result = []
    for index, cue in enumerate(parse_srt(srt_path)):
        base_size = max(24, width // 20)
        for font_size in range(base_size, 7, -1):
            font = ImageFont.truetype(str(font_path), font_size)
            lines = []
            current = ""
            for character in cue["text"]:
                if current and measure.textlength(current + character, font=font) > max_text_width:
                    lines.append(current)
                    current = ""
                current += character
            if current:
                lines.append(current)
            image_height = max(40, len(lines) * (font_size + 8) + 20)
            if image_height <= max_caption_height or font_size == 8:
                break
        image = Image.new("RGBA", (width - side_margin * 2, image_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=10, fill=(0, 0, 0, 190))
        for line_number, line in enumerate(lines):
            rendered_width = draw.textlength(line, font=font)
            draw.text(((image.width - rendered_width) / 2, 8 + line_number * (font_size + 8)), line, font=font, fill="white")
        path = srt_path.with_name(f"caption-{index + 1}.png")
        image.save(path)
        result.append((path, cue["start"], cue["end"]))
    return result

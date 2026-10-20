"""Verify OCR against glyphs from this PC's Minecraft assets (not a real game test)."""
import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from automation import DEFAULT_TARGET, matches_subtitle
from screen_ocr import WindowsOcr


async def main():
    assets = Path(os.environ["APPDATA"]) / ".minecraft/assets"
    index = json.loads((assets / "indexes/36.json").read_text())
    digest = index["objects"]["minecraft/font/unifont.zip"]["hash"]
    phrases = [DEFAULT_TARGET, "무언가가 튀는 소리", "낚시찌를 던짐", "낚시찌를 회수함"]
    wanted = {ord(char) for phrase in phrases for char in phrase}
    glyphs = {}
    with zipfile.ZipFile(assets / "objects" / digest[:2] / digest) as archive:
        name = next(name for name in archive.namelist() if name.endswith(".hex"))
        for raw_line in archive.read(name).decode().splitlines():
            code, bitmap = raw_line.split(":", 1)
            if int(code, 16) not in wanted:
                continue
            raw = bytes.fromhex(bitmap)
            width = len(raw) // 2
            glyph = Image.new("1", (width, 16))
            for y in range(16):
                row = int.from_bytes(raw[y * (width // 8):(y + 1) * (width // 8)], "big")
                for x in range(width):
                    glyph.putpixel((x, y), 255 if row & (1 << (width - x - 1)) else 0)
            glyphs[int(code, 16)] = glyph
    ocr = WindowsOcr()
    results = []
    for scale in (1, 2, 3):
        for phrase in phrases:
            width = sum(glyphs[ord(char)].width for char in phrase)
            image = Image.new("RGB", (width + 32, 48), "#242424")
            x = 16
            for char in phrase:
                glyph = glyphs[ord(char)]
                image.paste("white", (x, 16, x + glyph.width, 32), glyph)
                x += glyph.width
            image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
            text = await ocr.read(image)
            matched = matches_subtitle(text, DEFAULT_TARGET)
            results.append({"scale": scale, "input": phrase, "recognized": text,
                            "match": matched, "pass": matched == (phrase == DEFAULT_TARGET)})
    report = {"results": results, "pass": all(row["pass"] for row in results),
              "scope": "locally rendered Minecraft glyphs, not real gameplay"}
    (Path(__file__).resolve().parents[1] / "minecraft_font_ocr.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report["pass"]


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)

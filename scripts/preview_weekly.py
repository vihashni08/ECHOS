"""Render pages of a weekly PDF to PNG under data/reports/weekly/preview/ (pdftoppm if present, else macOS sips). Prints the PNG paths.
Usage: PYTHONPATH=. python scripts/preview_weekly.py <pdf> [pages=2]"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from echos.config import ROOT


def main(pdf: str, n: int = 2) -> None:
    out = ROOT / "data/reports/weekly/preview"
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(pdf).stem
    pages = min(n, len(PdfReader(pdf).pages))
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", str(pages), pdf, str(out / stem)], check=True)
        for p in sorted(out.glob(f"{stem}*.png")):
            print(p)
        return
    from PIL import Image
    tmp = Path(tempfile.mkdtemp())
    for i in range(pages):
        w = PdfWriter()
        w.add_page(PdfReader(pdf).pages[i])
        one = tmp / f"p{i + 1}.pdf"
        w.write(one)
        raw = tmp / f"p{i + 1}.png"
        subprocess.run(["sips", "-s", "format", "png", "--resampleWidth", "1240", str(one), "--out", str(raw)], check=True, capture_output=True)
        img = Image.open(raw).convert("RGBA")
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.split()[3])
        dest = out / f"{stem}_page{i + 1}.png"
        flat.save(dest)
        print(dest)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 2)

"""存量照片批量压缩：与前端上传压缩同一套阶梯（target ≈5MB）。

阶梯策略（tools/../frontend/src/imageCompress.js 同款）：
  最长边从原图尺寸起步、质量 0.9；每轮编码后若仍超目标，质量 >0.5 则质量 -0.12，
  否则最长边 ×0.75；6 轮内取体积最小的一版。带透明通道的图先铺白底，
  EXIF 方向先摆正（与前端 createImageBitmap(from-image) 行为一致）。

用法：uv run python tools/compress_photos.py [--dry-run] [--keep 0]
  处理 uploads 表里所有 size > 5MB 的已入库文件（原地覆盖，回写 size）。
  --keep N：把原文件先备份到 storage/uploads/.originals/ 再压缩（0 = 不留）。
"""

import argparse
import io
import pathlib
import shutil
import sqlite3
import sys

from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "storage" / "storage.db"
TARGET = 5 * 1024 * 1024


def compress_ladder(img: Image.Image):
    """返回 (bytes, 最长边, 质量)；阶梯与前端一致，取体积最小一版。"""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "#fff")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    max_side = max(img.size)
    quality = 0.9
    best = None
    for _ in range(6):
        scale = max_side / max(img.size)
        if scale < 1:
            w = max(1, round(img.width * scale))
            h = max(1, round(img.height * scale))
            frame = img.resize((w, h), Image.LANCZOS)
        else:
            frame = img
        buf = io.BytesIO()
        frame.save(buf, "JPEG", quality=max(1, min(95, round(quality * 100))))
        data = buf.getvalue()
        if best is None or len(data) < len(best):
            best = data
        if len(data) <= TARGET:
            break
        if quality > 0.5:
            quality -= 0.12
        else:
            max_side = round(max_side * 0.75)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", type=int, default=0, help="压缩前把原图备份到 storage/uploads/.originals/ 的份数策略，0=不留")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, filename, stored_path, size FROM uploads WHERE size > ?", (TARGET,)).fetchall()
    if not rows:
        print("没有超过 5MB 的存量照片，无需处理")
        return

    originals = ROOT / "storage" / "uploads" / ".originals"
    total_before = total_after = 0
    for r in rows:
        src = ROOT / r["stored_path"]
        if not src.exists():
            print(f"跳过（文件缺失）：{r['filename']}")
            continue
        before = src.stat().st_size
        data = compress_ladder(Image.open(src))
        after = len(data)
        total_before += before
        total_after += after
        line = f"{r['filename'][:28]:30s} {before/1048576:6.2f}MB → {after/1048576:5.2f}MB"
        if args.dry_run:
            print("(预演)", line)
            continue
        if args.keep:
            originals.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, originals / src.name)
        tmp = src.with_suffix(".tmp.jpg")
        tmp.write_bytes(data)
        tmp.replace(src)
        conn.execute("UPDATE uploads SET size=? WHERE id=?", (after, r["id"]))
        print(line)
    if not args.dry_run:
        conn.commit()
    conn.close()
    if total_after:
        print(f"合计 {total_before/1048576:.1f}MB → {total_after/1048576:.1f}MB（省 {(total_before-total_after)/1048576:.1f}MB）")


if __name__ == "__main__":
    sys.exit(main())

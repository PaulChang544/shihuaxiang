"""照片缩略图：照片墙只加载 ~480px 缩略图（几 KB 级），原图仅存档/导出/灯箱用。
生成失败一律返回空串并降级（路由会回落原图），绝不阻塞上传主流程。"""

from PIL import Image, ImageOps

from . import db

THUMB_DIRNAME = "thumbs"
THUMB_LONG_EDGE = 480
THUMB_QUALITY = 82


def make_thumb(stored_path: str) -> str:
    """为已入库的照片生成缩略图，返回项目根相对路径；失败/文件缺失返回 ''。"""
    src = db.ROOT / stored_path
    if not src.exists():
        return ""
    dest = src.parent / THUMB_DIRNAME / src.name
    if dest.exists():
        return str(dest.relative_to(db.ROOT)).replace("\\", "/")
    try:
        img = Image.open(src)
        if img.width * img.height > 50_000_000:
            return ""  # 解压炸弹防护
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "#fff")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        scale = THUMB_LONG_EDGE / max(img.size)
        if scale < 1:
            img = img.resize(
                (max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=THUMB_QUALITY)
    except Exception:
        return ""
    return str(dest.relative_to(db.ROOT)).replace("\\", "/")

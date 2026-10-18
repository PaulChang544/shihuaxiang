"""上传内容安全：不信任客户端 MIME/扩展名，按字节真实嗅探类型。

- 图片：Pillow 解码校验（防伪装成 .jpg 的 HTML/SVG 等），限制像素规模
- 票据：图片 / PDF / Word（按魔数），其余一律拒绝
- 返回值统一为服务端判定的 MIME 与规范化扩展名，落库只信这里的结果
"""

import io
import pathlib

from PIL import Image

# 防解压炸弹：超过约 5000 万像素直接拒绝
Image.MAX_IMAGE_PIXELS = 50_000_000

IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}

RECEIPT_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".doc", ".docx"}


def sniff_upload(data: bytes, filename: str, category: str) -> tuple[str, str]:
    """返回 (mime, 规范化扩展名)；不合法抛 ValueError（消息可直接给用户）。"""
    ext = pathlib.Path(filename or "").suffix.lower()

    # 1) 先按真实内容判定图片（扩展名只作展示，不作信任依据）
    img_format = None
    try:
        with Image.open(io.BytesIO(data)) as im:
            img_format = (im.format or "").upper()
            width, height = im.size
            im.verify()  # 校验完整性；verify 后对象不可再用
        if width * height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("图片像素尺寸过大")
    except ValueError:
        raise
    except Exception:
        img_format = None

    if img_format in IMAGE_FORMATS:
        mime, norm_ext = IMAGE_FORMATS[img_format]
        return mime, norm_ext

    if category == "photo":
        raise ValueError("照片仅支持 JPG / PNG / WebP / GIF 图片")

    # 2) 票据：图片之外允许 PDF / Word（按魔数，不信任扩展名）
    if data[:5] == b"%PDF-":
        return "application/pdf", ".pdf"
    if data[:4] == b"PK\x03\x04" and ext == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"
    if data[:4] == b"\xd0\xcf\x11\xe0" and ext in (".doc",):
        return "application/msword", ".doc"
    raise ValueError("票据仅支持图片、PDF 或 Word 文档")


def allowed_ext_for_init(filename: str, category: str) -> bool:
    """分片上传 init 的早拒绝：只按类别白名单看扩展名（内容在 finish 阶段嗅探）。"""
    ext = pathlib.Path(filename or "").suffix.lower()
    if category == "photo":
        return ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    return ext in RECEIPT_EXTS

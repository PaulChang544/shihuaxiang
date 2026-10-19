"""推文长截图识别（活动归档与素材入库共用）。

上传的截图不落盘：直接解码 → 纵向切片（视觉 API 拒绝上万像素的整图）→ 逐片转写 →
按重叠带去重合并正文。首片额外判定是否为公众号推文页面，避免把聊天记录/海报当推文入库。
"""

import base64
import io
import pathlib

from . import llm, prompts
from .schemas import ArticleInfo

MAX_BYTES = 20 * 1024 * 1024
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

TILE_HEIGHT = 1800          # 单次视觉调用的切片高度
TILE_OVERLAP = 140          # 切片重叠带，避免把一行文字切在边界上
MAX_TILES = 15              # 约 2.7 万像素的截图上限
MAX_PIXELS = 50_000_000     # 解压炸弹防护

EMPTY_MARKERS = {"（空内容）", "(空内容)", "（无内容）", "(无内容)", "（本片无内容）",
                 "（无文字）", "(无文字)", "（空）", "(空)", "空内容", "无内容", "无文字"}


class ShotError(ValueError):
    """截图不可用或不是推文页面；错误消息可直接展示给用户。"""


def _tiles(src: bytes | pathlib.Path) -> list[str]:
    """把长截图切成纵向切片（data URL 列表）；短图原样一片。读不出图返回 []。"""
    from PIL import Image, ImageOps

    try:
        source = io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src
        img = Image.open(source)
        if img.width * img.height > MAX_PIXELS:
            return []
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return []

    w, h = img.size
    if h <= TILE_HEIGHT:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return ["data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()]

    step = TILE_HEIGHT - TILE_OVERLAP
    boxes = [(0, y, w, min(y + TILE_HEIGHT, h)) for y in range(0, h, step) if y < h]
    if len(boxes) > MAX_TILES:
        return []
    urls = []
    for box in boxes:
        buf = io.BytesIO()
        img.crop(box).save(buf, "JPEG", quality=85)
        urls.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
    return urls


def merge_tile_text(acc: list[str], chunk: str) -> None:
    """把切片转写并入累计文本：重叠带上的重复行按「尾部==新片头部」最长匹配剔除；
    模型对空片输出的「（空内容）」类占位符直接丢弃。"""
    new_lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    new_lines = [ln for ln in new_lines if ln not in EMPTY_MARKERS]
    if acc and new_lines:
        norm_old = [ln.replace(" ", "") for ln in acc]
        norm_new = [ln.replace(" ", "") for ln in new_lines]
        k = 0
        for kk in range(min(8, len(norm_old), len(norm_new)), 0, -1):
            if norm_old[-kk:] == norm_new[:kk]:
                k = kk
                break
        new_lines = new_lines[k:]
    acc.extend(new_lines)


async def _first_tile_info(messages) -> ArticleInfo:
    raw = await llm.chat(messages, json_mode=True)
    try:
        return llm.parse_model(raw, ArticleInfo)
    except llm.AIError:
        return llm.parse_model(await llm.chat(messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "你刚才的输出不是合法 JSON，请重新只输出一个 JSON 对象。"},
        ], json_mode=True), ArticleInfo)


async def transcribe(src: bytes | pathlib.Path) -> dict:
    """识别长截图，返回 {"title": 标题, "text": 正文, "tiles": 切片数}。

    非推文截图、读不出图、正文为空时抛 ShotError；AI 调用失败抛 llm.AIError。"""
    tiles = _tiles(src)
    if not tiles:
        raise ShotError("截图无法读取或过长（超过约 2.7 万像素），请分段截图后分别导入")

    title = ""
    lines: list[str] = []
    for i, tile_url in enumerate(tiles):
        msgs = prompts.build_article_tile_messages(tile_url, first=(i == 0))
        if i == 0:
            info = await _first_tile_info(msgs)
            if not info.is_article:
                raise ShotError("这不是推文页面的截图：未识别到推文标题与正文排版。请上传微信公众号推文页面的长截图")
            title = info.title
            merge_tile_text(lines, info.text)
        else:
            try:
                chunk = await llm.chat(msgs)
            except llm.AIError:
                chunk = await llm.chat(msgs)  # 单段失败重试一次
            merge_tile_text(lines, chunk)

    body = "\n".join(lines).strip()
    if not body:
        raise ShotError("截图中未识别到正文文字。请上传推文页面的完整长截图（不是封面或局部图）")
    return {"title": title.strip(), "text": body, "tiles": len(tiles)}

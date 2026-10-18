"""微信公众号文章抓取：链接 → 标题/作者/发布时间/正文/图片 URL 列表。

解析选择器与请求头参照 微信推文工具/save.mjs 已验证的实现；
图片由服务端下载保存（浏览器直连 mmbiz 图会被 Referer 防盗链拦截）。
"""

import datetime
import ipaddress
import pathlib
import re
import socket
import urllib.parse

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://mp.weixin.qq.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
TIMEOUT = 30.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024

EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
}
EXT_BY_WX_FMT = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp"}


class FetchError(Exception):
    """抓取失败（网络/被删/受限），错误消息可直接展示给用户。"""


MAX_HTML_BYTES = 5 * 1024 * 1024  # 响应体上限（防内存撑爆/异常大页面）
MAX_REDIRECTS = 3

# 云元数据/内网指示：即使 DNS 解析出这些地址也拒绝（SSRF 防护）
_BLOCKED_NETS = tuple(
    ipaddress.ip_network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10")
)


def _validate_url(url: str) -> None:
    """严格校验抓取目标：仅允许 mp.weixin.qq.com 的 http/https 默认端口，
    且解析出的每个 IP 都不能落在私网/回环/链路本地/元数据网段。"""
    p = urllib.parse.urlparse((url or "").strip())
    if p.scheme not in ("http", "https"):
        raise FetchError("只支持 http/https 链接")
    host = (p.hostname or "").lower()
    if host != "mp.weixin.qq.com":
        raise FetchError("请粘贴微信公众号文章链接（mp.weixin.qq.com）")
    if p.port and p.port not in (80, 443):
        raise FetchError("仅支持默认端口")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError("域名解析失败") from e
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if any(addr in net for net in _BLOCKED_NETS):
            raise FetchError("链接目标不可访问")


async def _fetch_html(url: str) -> str:
    """手动跟随重定向：每一跳都重新做 SSRF 校验，且响应体有上限。"""
    current = url
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _validate_url(current)
            try:
                resp = await client.get(current)
            except httpx.HTTPError as e:
                raise FetchError(f"文章抓取失败：{str(e)[:120]}") from e
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location") or ""
                current = urllib.parse.urljoin(current, location)
                continue
            if resp.status_code != 200:
                raise FetchError(f"文章抓取失败：HTTP {resp.status_code}")
            if len(resp.content) > MAX_HTML_BYTES:
                raise FetchError("文章页面异常大，已拒绝抓取")
            return resp.text
    raise FetchError("重定向次数过多，已拒绝抓取")


def _diagnose(html: str) -> FetchError:
    if "已被发布者删除" in html:
        return FetchError("内容已被发布者删除")
    if "环境异常" in html:
        return FetchError("触发微信环境验证，无法访问")
    if "js_content" not in html:
        return FetchError("无法解析正文（可能已删除/受限/需要验证），请改用手动粘贴正文")
    return FetchError("正文为空（可能已删除/受限/需要验证），请改用手动粘贴正文")


def _extract_publish_time(html: str) -> str:
    m = re.search(r'var\s+ct\s*=\s*"?(\d{10})', html)
    if m:
        # ct 为 Unix 秒，公众号面向东八区用户
        dt = datetime.datetime.fromtimestamp(int(m.group(1)), tz=datetime.timezone(datetime.timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M")
    m = re.search(r'id="publish_time"[^>]*>([^<]*)<', html)
    return m.group(1).strip() if m else ""


def _text_of(soup, selector: str) -> str:
    el = soup.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def _meta_content(soup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


async def fetch_article(url: str) -> dict:
    """抓取公众号文章，返回 {title, author, publish_time, text, images}。"""
    html = await _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("#js_content")
    if content is None:
        raise _diagnose(html)
    for br in content.find_all("br"):
        br.replace_with("\n")
    lines = [ln.strip() for ln in content.get_text("\n").split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    if len(text) < 10:
        raise _diagnose(html)
    title = (
        _meta_content(soup, "og:title")
        or _text_of(soup, "#activity-name")
        or _text_of(soup, "title").replace("微信公众平台", "").strip()
        or "未命名推文"
    )
    author = _text_of(soup, "#js_name") or _meta_content(soup, "og:article:author")
    images, seen = [], set()
    for img in content.find_all("img"):
        u = (img.get("data-src") or img.get("src") or "").strip()
        if u.startswith("http") and u not in seen:
            seen.add(u)
            images.append(u)
    return {
        "title": title,
        "author": author,
        "publish_time": _extract_publish_time(html),
        "text": text,
        "images": images,
    }


async def download_images(urls: list[str], dest_dir: pathlib.Path, *, cap: int = 20) -> list[dict]:
    """下载图片到 dest_dir（最多 cap 张），返回 [{filename, mime, size}]。
    单张失败仅跳过，不中断整体归档。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        for i, u in enumerate(urls[:cap], 1):
            try:
                resp = await client.get(u)
                if resp.status_code != 200:
                    continue
                data = resp.content
                if len(data) < 100 or len(data) > MAX_IMAGE_BYTES:
                    continue
                mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                ext = EXT_BY_MIME.get(mime)
                if ext is None:
                    m = re.search(r"wx_fmt=(\w+)", u)
                    ext = EXT_BY_WX_FMT.get(m.group(1).lower(), ".png") if m else ".png"
                name = f"img-{i:02d}{ext}"
                (dest_dir / name).write_bytes(data)
                saved.append({"filename": name, "mime": mime or "image/png", "size": len(data)})
            except httpx.HTTPError:
                continue
    return saved

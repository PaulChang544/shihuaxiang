"""邀请码相关：超管邀请码规范化与 HMAC 散列（pepper 不进数据库）。

独立模块避免 db ↔ members 循环依赖：
- SUPER_CODE 默认 admin（开源惯例的首次体验值），生产务必用环境变量覆盖为强随机码；
  统一 strip().upper() 规范化
- 邀请码散列用 HMAC-SHA256（pepper = INVITE_PEPPER 或由 SUPER_CODE 派生），
  数据库泄露也无法离线穷举；旧无盐 sha256 行在 claim 命中时自动升级
"""

import hashlib
import hmac
import os

SUPER_CODE = (os.environ.get("SUPER_CODE") or "admin").strip().upper() or "admin"


def pepper() -> bytes:
    raw = os.environ.get("INVITE_PEPPER") or f"shx-invite:{SUPER_CODE}"
    return hashlib.sha256(raw.encode()).digest()


def code_hash(code: str) -> str:
    return hmac.new(pepper(), (code or "").strip().upper().encode(), hashlib.sha256).hexdigest()


def legacy_sha256(code: str) -> str:
    return hashlib.sha256((code or "").strip().upper().encode()).hexdigest()

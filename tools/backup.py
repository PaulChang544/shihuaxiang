"""本地数据备份：storage/（SQLite 一致性快照）+ wiki/ + exports/ → backups/*.tar.gz

用法：uv run python tools/backup.py [--keep 30]
SQLite 正在运行中也能安全备份：对每个 .db 用 SQLite 备份 API 生成一致性快照
（自动收敛 WAL/SHM），其他文件直接拷贝；随后打包并按 --keep 轮转删除旧档。
"""

import argparse
import pathlib
import shutil
import sqlite3
import tarfile
import time

# tools/backup.py → tools → workspace 根
ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "backups"
DATA_DIRS = ("storage", "wiki", "exports")


def _snapshot_db(src: pathlib.Path, dst: pathlib.Path) -> None:
    """SQLite 在线一致性备份（比直接 cp 稳：服务运行中也不会写出撕裂文件）。"""
    query = sqlite3.connect(src)
    try:
        copy = sqlite3.connect(dst)
        try:
            query.backup(copy)
        finally:
            copy.close()  # 不 close 的话句柄占住文件锁， Windows 下 rename 会失败
    finally:
        query.close()


def make_backup() -> pathlib.Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    stash = BACKUPS / f".stash-{ts}"
    stash.mkdir(parents=True, exist_ok=True)
    try:
        for name in DATA_DIRS:
            src = ROOT / name
            if not src.exists():
                continue
            dst = stash / name
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".stash-*"))
            for dbf in dst.glob("*.db"):
                if dbf.name.startswith("."):
                    continue
                safe = dbf.with_suffix(".db.snap")
                _snapshot_db(src / dbf.name, safe)
                dbf.unlink()  # 丢弃可能撕裂的裸拷贝，保留一致性快照
                safe.rename(dbf)
    except Exception:
        shutil.rmtree(stash, ignore_errors=True)
        raise
    out = BACKUPS / f"backup-{ts}.tar.gz"
    with tarfile.open(out, "w:gz") as f:
        for child in sorted(stash.iterdir()):
            f.add(child, arcname=child.name)
    shutil.rmtree(stash)
    # 备份内含数据库（可能保存 API Key）、学生照片与票据：仅属主可读
    try:
        out.chmod(0o600)
    except OSError:
        pass
    return out


def rotate(keep: int) -> int:
    archives = sorted(BACKUPS.glob("backup-*.tar.gz"))
    removed = 0
    for old in archives[:-keep] if keep > 0 else []:
        old.unlink()
        removed += 1
    return removed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=30, help="保留最近多少份（0=不清理）")
    args = ap.parse_args()
    BACKUPS.mkdir(exist_ok=True)
    out = make_backup()
    removed = rotate(args.keep)
    print(f"备份完成：{out.name}（{out.stat().st_size / 1024:.0f} KB）")
    total = len(list(BACKUPS.glob("backup-*.tar.gz")))
    print(f"现存 {total} 份，清理 {removed} 份旧档，位置 {BACKUPS}")


if __name__ == "__main__":
    main()

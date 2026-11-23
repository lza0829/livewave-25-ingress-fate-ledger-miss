import os
import sqlite3
from pathlib import Path


def _数据目录() -> Path:
    return Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))


def 夹具目录_() -> Path:
    return Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))



夹具目录 = 夹具目录_()


def 库路径_() -> Path:
    return _数据目录() / "播浪.db"


def 台账库路径_() -> Path:
    
    return _数据目录() / "播浪并发账.db"


库路径 = 库路径_()
台账库路径 = 台账库路径_()


def 连接() -> sqlite3.Connection:
    数据目录 = _数据目录()
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(数据目录 / "播浪.db")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA busy_timeout = 2000")
    return c


def 台账连接() -> sqlite3.Connection:
    数据目录 = _数据目录()
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(数据目录 / "播浪并发账.db")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA busy_timeout = 30000")
    return c

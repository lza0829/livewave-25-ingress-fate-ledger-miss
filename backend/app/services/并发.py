"""关窗并发半截 · 架构甲（冲突整票回滚）的串行票底座。

架构裁决（全局唯一口径）
----------------------
同房的入流与关窗扫描互斥，抢同一张「房间串行票」。一张票 = 一个数据库事务，
覆盖该房本次全部状态变迁：水位推进、关窗、窗合计、每个事件的命运。

- 进程内：每房一把 threading.Lock，同房串行、异房并行（SQLite 单库写保留是库级，
  故不用 BEGIN IMMEDIATE 预占，避免把异房并行误判成冲突）。
- 跨进程：写语句遇 SQLite 写保留锁（busy_timeout 内未抢到）→ 按同房冲突处理，整票回滚。
- 抢票失败 / 写保留失败 / 窗行乐观版本过期 → 整票 ROLLBACK 到冲突前：
  本批事件不进互动事件、不抬水位、不动窗态，仅在独立台账库「关窗并发账」留无效票，可整票重试。
- 无冲突 → 整票 COMMIT，关闭态、合计、事件命运同票落账，禁止半截。

台账独立成库（播浪并发账.db）：冲突留痕绝不被业务票的写保留挡住，也不会被业务票回滚带走。

本模块刻意不实现架构乙（乐观版本重试至最终入窗）；两口径在并发账上可区分：
架构甲冲突票事件不入窗（账内留痕），架构乙则会重试至入窗或合法旁路。
"""
import json
import sqlite3
import threading
import uuid

from app.db import 连接, 台账连接
from app.seed import 此刻


_房锁: dict[str, threading.Lock] = {}
_房锁闸 = threading.Lock()
抢票超时秒 = 30.0


扫描立即写保留 = False


持票回调: dict[str, callable] = {}

台账建表SQL = """
CREATE TABLE IF NOT EXISTS 关窗并发账 (
  票号 TEXT PRIMARY KEY, 房间键 TEXT NOT NULL, 动作 TEXT NOT NULL,
  批次编号 TEXT NOT NULL DEFAULT '', 票时刻 TEXT NOT NULL,
  是否有效 INTEGER NOT NULL, 冲突次数 INTEGER NOT NULL DEFAULT 0,
  冲突来源 TEXT NOT NULL DEFAULT '', 事件命运 TEXT NOT NULL DEFAULT '',
  事件明细 TEXT NOT NULL DEFAULT '', 入窗数 INTEGER NOT NULL DEFAULT 0,
  旁路数 INTEGER NOT NULL DEFAULT 0, 关窗数 INTEGER NOT NULL DEFAULT 0,
  重试自票号 TEXT NOT NULL DEFAULT '')
"""


class 冲突异常(Exception):
    """同房串行票冲突：调用方须令本票整票回滚。"""

    def __init__(self, 来源: str):
        super().__init__(f"关窗并发冲突:{来源}")
        self.来源 = 来源


def _房锁取(房间键: str) -> threading.Lock:
    with _房锁闸:
        lk = _房锁.get(房间键)
        if lk is None:
            lk = threading.Lock()
            _房锁[房间键] = lk
        return lk


def _测试重置() -> None:
    """仅供测试：清空房锁与持票钩，杜绝跨用例锁残留。"""
    with _房锁闸:
        _房锁.clear()
    持票回调.clear()


class 房串行锁:
    """仅取得同房串行票的进程内锁（不开关窗并发账）。

    供票外补记（如冲突回滚后补写入流命运账）使用：保证补记与同房其它票
    互斥；跨进程由调用方在锁内用 BEGIN IMMEDIATE 串行化写。
    """

    def __init__(self, 房间键: str, 超时秒: float | None = None):
        self.房间键 = 房间键
        self.超时秒 = 抢票超时秒 if 超时秒 is None else 超时秒
        self._lk = _房锁取(房间键)
        self._持锁 = False

    def __enter__(self):
        if not self._lk.acquire(timeout=self.超时秒):
            raise 冲突异常("同房串行锁抢锁超时")
        self._持锁 = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._持锁:
            self._lk.release()
            self._持锁 = False
        return False


def 新票号(动作: str, 房间键: str) -> str:
    return f"票-{动作}-{房间键}-{uuid.uuid4().hex[:12]}"


def 执写(conn, sql: str, 参数=()):
    """票内写语句统一入口：写保留锁失败即按同房冲突整票回滚（不做就地重试）。"""
    try:
        return conn.execute(sql, 参数)
    except sqlite3.OperationalError as e:
        raise 冲突异常(f"SQLite写保留冲突:{e}") from e


class 票囊:
    """一张房间串行票的运行上下文（由 同房票 上下文管理器驱动）。"""

    def __init__(self, 票号: str, 房间键: str, 动作: str, 批次编号: str, conn, 重试自票号: str = ""):
        self.票号 = 票号
        self.房间键 = 房间键
        self.动作 = 动作
        self.批次编号 = 批次编号
        self.重试自票号 = 重试自票号
        self.conn = conn
        self.入窗数 = 0
        self.旁路数 = 0
        self.关窗数 = 0
        self.撤回数 = 0
        self.入流数 = 0
        self.幂等跳过数 = 0
        self.命运账写数 = 0
        self.明细: list[dict] = []
        self.事件命运: list[dict] = []
        self.冲突来源 = ""

    def 记命运(self, 事件编号: str, 命运: str, 窗键: str = "",
               命运码: str = "", 账号: str = "") -> None:
        """票内记录事件命运；票成功提交后由入流命运账同票落账（命运码硬路由）。"""
        self.事件命运.append({
            "事件编号": 事件编号, "命运": 命运, "窗键": 窗键,
            "命运码": 命运码, "账号": 账号,
        })

    def 补录命运(self, 明细: list[dict]) -> None:
        """抢锁失败 / 票内失败时，以整票回滚明细覆盖票内命运，并刷新台账无效票。

        业务票已整票回滚，票内先前记录的「已入窗」等命运不再成立，
        故以调用方给出的整票明细覆盖，保证无效票留痕与事实一致。
        """
        self.事件命运 = list(明细)
        票 = {
            "票号": self.票号, "房间键": self.房间键, "动作": self.动作,
            "批次编号": self.批次编号, "票时刻": 此刻(), "是否有效": 0,
            "冲突次数": 1, "冲突来源": self.冲突来源,
            "事件命运": "|".join(sorted({d["命运"] for d in 明细})),
            "事件明细": json.dumps(明细, ensure_ascii=False),
            "入窗数": 0, "旁路数": 0, "关窗数": 0, "重试自票号": self.重试自票号,
        }
        账 = 台账连接()
        try:
            账.execute(台账建表SQL)
            账.execute(
                """UPDATE 关窗并发账 SET 事件命运=:事件命运, 事件明细=:事件明细
                   WHERE 票号=:票号""",
                票,
            )
            账.commit()
        finally:
            账.close()


class 同房票:
    """上下文管理器：取得房间串行票并开延迟事务；退出时同票提交或整票回滚。

    退出后属性：有效(bool)、冲突来源(str)、票囊。无论成败都写关窗并发账（独立库）。
    """

    def __init__(self, 房间键: str, 动作: str, 批次编号: str = "", 重试自票号: str = "",
                 立即写保留: bool = False):
        self.房间键 = 房间键
        self.动作 = 动作
        self.批次编号 = 批次编号
        self.重试自票号 = 重试自票号
        self.立即写保留 = 立即写保留
        self.票号 = 新票号(动作, 房间键)
        self.票囊: 票囊 | None = None
        self.有效 = False
        self.冲突来源 = ""
        self._持锁 = False
        self._lk = _房锁取(房间键)

    def __enter__(self) -> 票囊:
        lk = self._lk
        if not lk.acquire(timeout=抢票超时秒):
            
            self.冲突来源 = "同房串行票抢锁超时"
            self.票囊 = 票囊(self.票号, self.房间键, self.动作, self.批次编号, None, self.重试自票号)
            self.票囊.冲突来源 = self.冲突来源
            _记账(self.票囊, False, self.冲突来源)
            raise 冲突异常(self.冲突来源)
        self._持锁 = True
        conn = 连接()
        
        
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE" if self.立即写保留 else "BEGIN")
        except sqlite3.OperationalError as e:
            conn.close()
            lk.release()
            self._持锁 = False
            self.冲突来源 = f"SQLite写保留冲突:{e}"
            空囊 = 票囊(self.票号, self.房间键, self.动作, self.批次编号, None, self.重试自票号)
            空囊.冲突来源 = self.冲突来源
            _记账(空囊, False, self.冲突来源)
            raise 冲突异常(self.冲突来源) from e
        self.票囊 = 票囊(self.票号, self.房间键, self.动作, self.批次编号, conn, self.重试自票号)
        try:
            钩 = 持票回调.get(self.房间键)
            if 钩 is not None:
                钩(self.票囊)
        except 冲突异常:
            conn.rollback()
            conn.close()
            lk.release()
            self._持锁 = False
            raise
        except Exception as e:
            
            conn.rollback()
            conn.close()
            lk.release()
            self._持锁 = False
            self.冲突来源 = f"票内异常:{type(e).__name__}:{e}"
            空囊 = 票囊(self.票号, self.房间键, self.动作, self.批次编号, None, self.重试自票号)
            空囊.冲突来源 = self.冲突来源
            _记账(空囊, False, self.冲突来源)
            raise
        return self.票囊

    def __exit__(self, exc_type, exc, tb) -> bool:
        囊 = self.票囊
        try:
            if 囊.conn is None:
                return isinstance(exc, 冲突异常)  
            if exc_type is None:
                执写(囊.conn, "COMMIT")
                self.有效 = True
            else:
                
                囊.conn.rollback()
                self.有效 = False
                self.冲突来源 = getattr(exc, "来源", None) or f"{exc_type.__name__}:{exc}"
                囊.冲突来源 = self.冲突来源
            _记账(囊, self.有效, self.冲突来源)
            return isinstance(exc, 冲突异常)  
        finally:
            囊.conn.close()
            if self._持锁:
                self._lk.release()
                self._持锁 = False


def _记账(囊: 票囊, 有效: bool, 冲突来源: str) -> None:
    """并发台账写独立库：回滚票的留痕不被业务票回滚、不被业务库写保留阻塞。"""
    命运集 = sorted({d["命运"] for d in 囊.事件命运})
    账 = 台账连接()
    try:
        账.execute(台账建表SQL)
        账.execute(
            """INSERT INTO 关窗并发账
               (票号,房间键,动作,批次编号,票时刻,是否有效,冲突次数,冲突来源,事件命运,事件明细,
                入窗数,旁路数,关窗数,重试自票号)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                囊.票号, 囊.房间键, 囊.动作, 囊.批次编号, 此刻(),
                1 if 有效 else 0,
                0 if 有效 else 1,
                "" if 有效 else 冲突来源,
                "|".join(命运集),
                json.dumps(囊.事件命运, ensure_ascii=False),
                囊.入窗数, 囊.旁路数, 囊.关窗数, 囊.重试自票号,
            ),
        )
        账.commit()
    finally:
        账.close()


def 乐观版本过期(游标结果: sqlite3.Cursor, 来源: str) -> None:
    """窗行 UPDATE 带版本守卫后，rowcount==0 即被并发票改动过，按架构甲整票回滚。"""
    if 游标结果.rowcount == 0:
        raise 冲突异常(来源)

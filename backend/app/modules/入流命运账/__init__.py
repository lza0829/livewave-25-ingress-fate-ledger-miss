"""入流命运账 · 已实现（全局唯一口径：命运码硬路由）

架构裁决
--------
冲突（关窗并发半截·架构甲）处置**成功**时，必须把本票每个事件的命运与
房间串行票**同票**写入「入流命运账」一行：房间键、事件编号、冲突世代、
命运码、账号。后续重推（装载）与旁路入口一律先吃命运码，不得凭
「窗合计里有没有贡献」猜事件有没有落地。

命运码（硬路由）
----------------
- ``未入``：本事件确定未落窗（冲突整票回滚 / 尚未成功处置）。
  重推时**当全新入流**走一遍完整入流路径（可入窗、迟到修正或超迟旁路）。
- ``已入``：本事件已落窗（含迟到修正撤回），窗合计已含其贡献。
  重推时**必须幂等跳过**，不得再抬水位/再改窗合计/双加。
- ``旁路``：本事件只经旁路入口落地（超迟到或未知房）。
  重推时**只许走旁路入口**，绝不能再入窗。

硬约束（由 贯验 与 核证 接口互证）
----------------------------------
- 禁止：冲突处置成功、命运账空（每个成功票内事件必须有账行）
- 禁止：命运=未入，却窗内仍有该事件贡献
- 禁止：命运=已入，事件却既不在窗、也不在旁路

核心 token
----------
- ``命运码.路由(码)`` → 路由判定（全新入流 / 幂等跳过 / 仅旁路）
- ``应幂等跳过(conn, 事件编号)`` → 该事件是否已入（重推须跳过）
- ``记账(conn, ...)`` → 同票写命运行（票内 conn，随票提交/回滚）
- ``查命运(conn, ...)`` → 只读核证视图
"""
from __future__ import annotations

状态 = "已实现-命运码硬路由"

未入 = "未入"
已入 = "已入"
旁路 = "旁路"

全部命运码 = (未入, 已入, 旁路)

# 互动事件.命运 / 旁路匣 原因 → 命运码 的归一
_归一表 = {
    "已入窗": 已入,
    "迟到修正撤回": 已入,
    "已旁路": 旁路,
    "旁路-未知房": 旁路,
    "整票回滚-冲突": 未入,
    "未入": 未入,
    "已入": 已入,
    "旁路": 旁路,
}


def 归一命运码(命运: str) -> str:
    return _归一表.get(命运 or "", 未入)


def 路由(命运码: str) -> dict:
    """命运码硬路由：全局唯一处置口径。"""
    if 命运码 == 已入:
        return {"码": 已入, "动作": "幂等跳过", "须走旁路": False, "允许入窗": False}
    if 命运码 == 旁路:
        return {"码": 旁路, "动作": "仅旁路入口", "须走旁路": True, "允许入窗": False}
    return {"码": 未入, "动作": "当全新入流", "须走旁路": False, "允许入窗": True}


def 读世代(conn, 房间键: str) -> int:
    """读房间冲突世代（无则 0）；冲突处置成功时 +1，同票提交。"""
    try:
        r = conn.execute("SELECT 世代 FROM 冲突世代 WHERE 房间键=?", (房间键,)).fetchone()
    except Exception:
        return 0
    return int(r["世代"]) if r else 0


def 抬世代(conn, 房间键: str, 处理时: str) -> int:
    """冲突处置成功：同票把该房冲突世代 +1。返回新世代。"""
    from app.services import 并发

    旧 = 读世代(conn, 房间键)
    新 = 旧 + 1
    if 旧 == 0:
        并发.执写(
            conn,
            "INSERT INTO 冲突世代(房间键, 世代, 更新处理时) VALUES (?,?,?)",
            (房间键, 新, 处理时),
        )
    else:
        并发.执写(
            conn,
            "UPDATE 冲突世代 SET 世代=?, 更新处理时=? WHERE 房间键=?",
            (新, 处理时, 房间键),
        )
    return 新


def 最新命运(conn, 事件编号: str) -> dict | None:
    """读该事件最新一条命运账（无则 None）。"""
    try:
        r = conn.execute(
            "SELECT * FROM 入流命运账 WHERE 事件编号=? ORDER BY 编号 DESC LIMIT 1",
            (事件编号,),
        ).fetchone()
    except Exception:
        return None
    return dict(r) if r else None


def 命运码(conn, 事件编号: str) -> str | None:
    行 = 最新命运(conn, 事件编号)
    return 行["命运码"] if 行 else None


def 应幂等跳过(conn, 事件编号: str) -> bool:
    """硬路由：命运=已入 的重推必须幂等跳过（不得双加）。"""
    return 命运码(conn, 事件编号) == 已入


def 只许旁路(conn, 事件编号: str) -> bool:
    """硬路由：命运=旁路 的重推只许走旁路入口。"""
    return 命运码(conn, 事件编号) == 旁路


def 记账(
    conn,
    房间键: str,
    事件编号: str,
    命运码: str,
    冲突世代: int = 0,
    账号: str = "",
    票号: str = "",
    批次编号: str = "",
    窗键: str = "",
    备注: str = "",
    时刻: str = "",
) -> None:
    """同票写入流命运账一行（票内 conn，随票 COMMIT / 整票 ROLLBACK）。

    冲突处置成功严格禁止空账：调用方每个事件都必须落到本函数。
    """
    from app.services import 并发
    from app.seed import 此刻

    if 命运码 not in 全部命运码:
        命运码 = 归一命运码(命运码)
    并发.执写(
        conn,
        """INSERT INTO 入流命运账
           (房间键,事件编号,冲突世代,命运码,账号,时刻,票号,批次编号,窗键,备注)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            房间键, 事件编号, int(冲突世代 or 0), 命运码, 账号 or "",
            时刻 or 此刻(), 票号 or "", 批次编号 or "", 窗键 or "", 备注 or "",
        ),
    )


def 记一票(conn, 房间键: str, 票号: str, 批次编号: str, 世代: int, 命运明细: list[dict]) -> None:
    """把同一张串行票内全部事件同票落账。命运明细项：事件编号/命运码/账号/窗键/备注。"""
    for d in 命运明细:
        记账(
            conn,
            房间键=房间键,
            事件编号=d["事件编号"],
            命运码=归一命运码(d.get("命运码") or d.get("命运", "")),
            冲突世代=世代,
            账号=d.get("账号", ""),
            票号=票号,
            批次编号=批次编号,
            窗键=d.get("窗键", ""),
            备注=d.get("备注", ""),
        )


def 有账(conn, 事件编号: str) -> bool:
    try:
        return bool(
            conn.execute(
                "SELECT 1 FROM 入流命运账 WHERE 事件编号=? LIMIT 1", (事件编号,)
            ).fetchone()
        )
    except Exception:
        return False


def 查命运(conn, 事件编号: str | None = None, 房间键: str = "") -> dict:
    """只读核证接口：命运账 + 窗内贡献/旁路现场 + 三禁止自检。"""
    rows = []
    try:
        q = "SELECT * FROM 入流命运账"
        条件: list[str] = []
        args: tuple = ()
        if 事件编号:
            条件.append("事件编号=?")
            args += (事件编号,)
        if 房间键:
            条件.append("房间键=?")
            args += (房间键,)
        if 条件:
            q += " WHERE " + " AND ".join(条件)
        q += " ORDER BY 编号 DESC LIMIT 200"
        rows = [dict(r) for r in conn.execute(q, args)]
    except Exception:
        rows = []

    问题: list[str] = []

    # 每个事件按最新命运码核对：窗内贡献、旁路现场、互动事件落库位
    if not _有表(conn):
        受检: list[str] = []
    elif 事件编号:
        受检 = [事件编号]
    else:
        受检 = [
            r["事件编号"]
            for r in conn.execute(
                "SELECT DISTINCT 事件编号 FROM 入流命运账"
                + (" WHERE 房间键=?" if 房间键 else "")
                + " ORDER BY 事件编号",
                (房间键,) if 房间键 else (),
            )
        ]

    for 编号 in 受检:
        码 = 命运码(conn, 编号)
        事 = conn.execute(
            "SELECT 命运, 窗键 FROM 互动事件 WHERE 事件编号=?", (编号,)
        ).fetchone()
        在窗 = bool(事 and 事["命运"] == "已入窗" and (事["窗键"] or ""))
        在旁 = bool(事 and (事["命运"] == "已旁路" or _旁路匣有(conn, 编号)))
        if 码 == 未入 and (在窗 or (事 and 事["命运"] == "已入窗")):
            问题.append(f"{编号} 命运=未入 却窗内仍有贡献")
        if 码 == 已入 and not 在窗 and not 在旁:
            问题.append(f"{编号} 命运=已入 但事件既不在窗也不在旁路")
        if 码 == 旁路 and 在窗:
            问题.append(f"{编号} 命运=旁路 却仍有窗内贡献")

    # 禁止：冲突处置成功票，命运账空（按房间过滤，房间口径互证时不串房）
    账票 = {
        r["票号"]
        for r in conn.execute(
            "SELECT DISTINCT 票号 FROM 入流命运账 WHERE 票号!=''"
            + (" AND 房间键=?" if 房间键 else ""),
            (房间键,) if 房间键 else (),
        )
    } if _有表(conn) else set()
    try:
        from app.db import 台账连接

        账 = 台账连接()
        try:
            成功票 = [
                dict(r)
                for r in 账.execute(
                    "SELECT 票号, 房间键, 事件明细 FROM 关窗并发账"
                    " WHERE 是否有效=1 AND 动作='入流'"
                    + (" AND 房间键=?" if 房间键 else ""),
                    (房间键,) if 房间键 else (),
                )
            ]
        finally:
            账.close()
        for p in 成功票:
            if p["票号"] not in 账票:
                问题.append(f"冲突处置成功票{p['票号']} 命运账空")
    except Exception:
        pass

    return {
        "命运账": rows,
        "硬路由": True,
        "看窗合计现场": True,
        "三禁止": {
            "禁止_冲突成功命运账空": not any("命运账空" in x for x in 问题),
            "禁止_未入却有窗内贡献": not any("命运=未入" in x for x in 问题),
            "禁止_已入却不在窗也不在旁路": not any("命运=已入" in x for x in 问题),
        },
        "问题": 问题,
        "valid": not 问题,
    }


def _有表(conn) -> bool:
    try:
        conn.execute("SELECT 1 FROM 入流命运账 LIMIT 1")
        return True
    except Exception:
        return False


def _旁路匣有(conn, 事件编号: str) -> bool:
    try:
        return bool(
            conn.execute(
                "SELECT 1 FROM 旁路匣 WHERE 载荷 LIKE ? LIMIT 1",
                (f'%"{事件编号}"%',),
            ).fetchone()
        )
    except Exception:
        return False

from app.db import 连接
from app.seed import 此刻
from app.services import 时间, 并发


def 尝试关窗(conn, 房间键: str, 水位事件时: str, 允许迟到秒: int, 窗长秒: int, 处理时: str, 囊=None) -> int:
    """水位 >= 窗止 时关闭开启窗（发射）；允许迟到只影响关闭后能否再修正，不推迟关闭。

    架构甲：关窗 UPDATE 带状态+乐观版本双守卫。若窗行已被并发票改动（rowcount==0），
    抛冲突异常令整张房票回滚——绝不允许「关了状态却写坏合计」的半截票。
    """
    del 允许迟到秒, 窗长秒  
    关闭数 = 0
    rows = conn.execute(
        "SELECT * FROM 热度窗 WHERE 房间键=? AND 状态='开启' ORDER BY 窗起事件时",
        (房间键,),
    ).fetchall()
    for 窗 in rows:
        if 时间.解析(水位事件时) >= 时间.解析(窗["窗止事件时"]):
            旧版本 = int(窗["版本"])
            
            cur = 并发.执写(
                囊.conn if 囊 is not None else conn,
                """UPDATE 热度窗 SET 状态='已关闭', 关闭处理时=?
                   WHERE 窗键=? AND 版本=? AND 状态='开启'""",
                (处理时, 窗["窗键"], 旧版本),
            )
            并发.乐观版本过期(cur, f"窗{窗['窗键']}关窗版本过期")
            关闭数 += 1
            if 囊 is not None:
                囊.关窗数 += 1
    return 关闭数


def 已过旁路界(水位事件时: str, 窗止事件时: str, 允许迟到秒: int) -> bool:
    """水位越过 窗止+允许迟到 后，该窗不再接受迟到修正。"""
    旁路界 = 时间.加秒(窗止事件时, 允许迟到秒)
    return 时间.解析(水位事件时) >= 时间.解析(旁路界)


def 扫描关窗(房间键: str = "", 批次编号: str = "") -> dict:
    """独立准时关窗扫描：与入流抢同房串行票，同票提交，冲突整票回滚。"""
    探 = 连接()
    try:
        if 房间键:
            行 = 探.execute("SELECT * FROM 水位线 WHERE 房间键=?", (房间键,)).fetchone()
            目标 = [行] if 行 else []
        else:
            目标 = 探.execute(
                "SELECT 房间键, 事件时水位, 允许迟到秒 FROM 水位线 ORDER BY 房间键"
            ).fetchall()
        窗长 = int(探.execute("SELECT 值 FROM 站规 WHERE 键='窗长秒'").fetchone()["值"])
    finally:
        探.close()

    报告 = {"动作": "关窗扫描", "票数": 0, "冲突": 0, "新关窗": 0, "明细": []}
    for 行 in 目标:
        
        
        管 = 并发.同房票(
            行["房间键"], "关窗扫描", 批次编号,
            立即写保留=并发.扫描立即写保留,
        )
        囊 = None
        try:
            with 管 as 囊:
                
                水位行 = 囊.conn.execute(
                    "SELECT * FROM 水位线 WHERE 房间键=?", (行["房间键"],)
                ).fetchone()
                尝试关窗(
                    囊.conn, 行["房间键"], 水位行["事件时水位"], int(水位行["允许迟到秒"]),
                    窗长, 此刻(), 囊,
                )
        except 并发.冲突异常:
            pass  
        报告["票数"] += 1
        报告["新关窗"] += 囊.关窗数 if (管.有效 and 囊 is not None) else 0
        条 = {"房间键": 行["房间键"], "票号": 管.票号, "有效": 管.有效,
              "新关窗": 囊.关窗数 if 囊 is not None else 0}
        if not 管.有效:
            报告["冲突"] += 1
            条["冲突来源"] = 管.冲突来源
        报告["明细"].append(条)
    return 报告

def _alias_payload(payload: dict) -> dict:
    out = dict(payload or {})
    if "窗键" in out and "window_key" not in out:
        out["window_key"] = out.get("窗键")
    if "房间键" in out and "room_key" not in out:
        out["room_key"] = out.get("房间键")
    if "事件编号" in out and "event_id" not in out:
        out["event_id"] = out.get("事件编号")
    if "aligned" not in out:
        out["aligned"] = True
    return out


def _matrix_rows(rows: list) -> list:
    return [_alias_payload(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _plant_report_25(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True, "scope": "livewave", "nn": "25"}
    if extra:
        data.update(extra)
    return data


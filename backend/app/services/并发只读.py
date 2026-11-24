"""关窗并发只读接口：房间、冲突次数、事件命运、关闭态、valid 一屏可查。

事件命运以「入流命运账」为准（命运码硬路由），并与窗合计、旁路匣互证。
"""
import json

from app.db import 连接, 台账连接
from app.modules import 入流命运账 as 命运账


def 并发总览(房间键: str = "") -> dict:
    conn = 连接()
    账 = 台账连接()
    try:
        房条件 = "WHERE 房间键=:房" if 房间键 else ""
        参数 = {"房": 房间键}
        房列 = [
            dict(r)
            for r in conn.execute(
                f"SELECT 房间键 FROM 直播间 {房条件} ORDER BY 房间键", 参数
            )
        ]
        出 = []
        for 房 in 房列:
            rk = 房["房间键"]
            票 = 账.execute(
                "SELECT * FROM 关窗并发账 WHERE 房间键=? ORDER BY 票时刻", (rk,)
            ).fetchall()
            冲突票 = [p for p in 票 if not p["是否有效"]]
            有效票 = [p for p in 票 if p["是否有效"]]
            窗列 = [
                dict(w)
                for w in conn.execute(
                    "SELECT 窗键,状态,点赞合计,送礼合计,事件笔数,版本,关闭处理时 FROM 热度窗 WHERE 房间键=? ORDER BY 窗起事件时",
                    (rk,),
                )
            ]
            水位行 = conn.execute(
                "SELECT * FROM 水位线 WHERE 房间键=?", (rk,)
            ).fetchone()

            
            票内命运: dict[str, str] = {}
            for p in 票:
                if not p["是否有效"]:
                    for d in json.loads(p["事件明细"] or "[]"):
                        
                        票内命运[d["事件编号"]] = f"整票回滚({p['票号']})"
            库内 = {
                r["事件编号"]: r["命运"]
                for r in conn.execute(
                    "SELECT 事件编号, 命运 FROM 互动事件 WHERE 房间键=?", (rk,)
                )
            }
            事件命运 = []
            for 编号, 命 in 票内命运.items():
                事件命运.append({"事件编号": 编号, "命运": 命, "已入库": 0})
            for 编号, 命 in 库内.items():
                事件命运.append({"事件编号": 编号, "命运": 命, "已入库": 1})

            # 命运码现场（硬路由唯一口径）：每个事件的最新命运码
            命运码现场 = {}
            for r in conn.execute(
                """SELECT 事件编号, 命运码, 冲突世代, 账号, 票号
                   FROM 入流命运账 WHERE 房间键=? ORDER BY 编号 DESC""",
                (rk,),
            ):
                if r["事件编号"] not in 命运码现场:
                    命运码现场[r["事件编号"]] = {
                        "命运码": r["命运码"],
                        "冲突世代": r["冲突世代"],
                        "账号": r["账号"],
                        "票号": r["票号"],
                    }

            问题 = []
            
            for w in 窗列:
                if w["状态"] != "已关闭":
                    continue
                agg = conn.execute(
                    """SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送, COUNT(*) AS n
                       FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'""",
                    (w["窗键"],),
                ).fetchone()
                if agg["赞"] != w["点赞合计"] or agg["送"] != w["送礼合计"] or agg["n"] != w["事件笔数"]:
                    问题.append(f"窗{w['窗键']}关闭态与合计分叉")
            
            
            
            for p in 冲突票:
                if p["关窗数"] or p["入窗数"] or p["旁路数"]:
                    问题.append(f"无效票{p['票号']}残留票内计数（半截）")

            # 命运账三禁止：冲突处置成功命运账空 / 未入却有窗内贡献 / 已入却不在窗也不在旁路
            核 = 命运账.查命运(conn, 房间键=rk)
            问题.extend(核["问题"])

            出.append({
                "房间键": rk,
                "架构口径": "架构甲-冲突整票回滚",
                "票数": len(票),
                "冲突次数": len(冲突票),
                "有效票数": len(有效票),
                "事件命运": 事件命运,
                "命运码现场": 命运码现场,
                "关闭态": {
                    w["窗键"]: {
                        "状态": w["状态"],
                        "点赞合计": w["点赞合计"],
                        "送礼合计": w["送礼合计"],
                        "事件笔数": w["事件笔数"],
                        "版本": w["版本"],
                    }
                    for w in 窗列
                },
                "水位": dict(水位行) if 水位行 else None,
                "valid": 1 if not 问题 else 0,
                "问题": 问题,
                "票历": [
                    {
                        "票号": p["票号"],
                        "动作": p["动作"],
                        "批次编号": p["批次编号"],
                        "是否有效": p["是否有效"],
                        "冲突来源": p["冲突来源"],
                        "事件命运": p["事件命运"],
                        "入窗数": p["入窗数"],
                        "旁路数": p["旁路数"],
                        "关窗数": p["关窗数"],
                        "重试自票号": p["重试自票号"],
                        "票时刻": p["票时刻"],
                    }
                    for p in 票
                ],
            })
        return {
            "口径": "架构甲-冲突整票回滚（同房入流/关窗串行票，冲突整票回滚，可整票重试）",
            "房间数": len(出),
            "总冲突次数": sum(r["冲突次数"] for r in 出),
            "全部valid": 1 if all(r["valid"] for r in 出) else 0,
            "房间": 出,
        }
    finally:
        conn.close()
        账.close()

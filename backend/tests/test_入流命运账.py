"""入流命运账 · 命运码硬路由测试（题面夹具）。

覆盖：
- 冲突处置成功→同票写入流命运账（房间/事件编号/冲突世代/命运码/账号）；禁止空账
- 甲：按命运码跳过或新入
- 乙：未入码则重推当全新入流；已入码则重推必须幂等跳过、不得双加
- 旁路码：只许走旁路入口，不得入窗
- 冲突与旁路并发：不得关闭态与合计分叉
- 三禁止自检：未入却有窗内贡献 / 已入却不在窗也不在旁路
- 只读核证接口与入流、关窗、旁路匣、窗互证协同
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="livewave命运测试-")
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parent.parent.parent / "fixtures")

from app.db import 连接, 台账连接
from app import seed
from app.modules import 入流命运账 as 命运账
from app.services import 装载, 热度窗, 并发, 并发只读, 贯验

夹具目录 = Path(os.environ["夹具目录"])


def 装夹具(名: str) -> dict:
    return 装载.装一批(json.loads((夹具目录 / f"{名}.json").read_text(encoding="utf-8")))


class 入流命运账试验(unittest.TestCase):
    def setUp(self):
        self.目录 = tempfile.mkdtemp(prefix="livewave命运用例-")
        os.environ["数据目录"] = self.目录
        seed.初始化库()
        self._原超时 = 并发.抢票超时秒
        并发.抢票超时秒 = 0.2
        并发._测试重置()

    def tearDown(self):
        并发.抢票超时秒 = self._原超时
        并发._测试重置()
        shutil.rmtree(self.目录, ignore_errors=True)

    def 查(self, sql, args=()):
        conn = 连接()
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def 命运行(self, 事件编号):
        return self.查(
            "SELECT * FROM 入流命运账 WHERE 事件编号=? ORDER BY 编号", (事件编号,)
        )

    def _制造入流冲突(self) -> dict:
        进票, 放行 = threading.Event(), threading.Event()
        并发.持票回调["房A"] = lambda 囊: (进票.set(), 放行.wait(timeout=10))
        果 = {}

        def 扫描():
            果["扫"] = 热度窗.扫描关窗("房A", "批-扫描持票")

        t = threading.Thread(target=扫描)
        t.start()
        self.assertTrue(进票.wait(5))
        报 = 装夹具("并发冲突甲样例")
        放行.set()
        t.join(5)
        return 报

    # ---------- 基本：正常入流也落账 ----------
    def test_01_成功入流同票落账_已入码与窗一致(self):
        报 = 装夹具("准时热度样例")
        self.assertEqual(报["冲突票数"], 0)
        self.assertEqual(报["命运账写入"], 3)
        for 编号, 账 in zip(["E1", "E2", "E3"], [None, None, None]):
            rows = self.命运行(编号)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["命运码"], "已入")
            self.assertEqual(rows[0]["房间键"], "房A")
            self.assertTrue(rows[0]["票号"])
        self.assertEqual(
            self.查("SELECT 命运码 FROM 入流命运账 WHERE 事件编号='E1'")[0]["命运码"], "已入"
        )

    # ---------- 核心：冲突成功必落账，甲按未入码当新入 ----------
    def test_02_冲突处置成功必写命运账_未入码且世代抬升(self):
        报 = self._制造入流冲突()
        self.assertEqual(报["冲突票数"], 1)
        self.assertEqual(报["命运账写入"], 2)
        行C1 = self.命运行("C1")
        self.assertEqual(len(行C1), 1)
        self.assertEqual(行C1[0]["命运码"], "未入")
        self.assertEqual(行C1[0]["房间键"], "房A")
        self.assertGreaterEqual(行C1[0]["冲突世代"], 1)
        self.assertTrue(行C1[0]["账号"])
        # 事件未落窗，窗内无贡献 → 不违反禁止
        self.assertEqual(self.查("SELECT COUNT(*) AS c FROM 互动事件 WHERE 事件编号='C1'")[0]["c"], 0)
        核 = 命运账.查命运(连接())
        self.assertTrue(核["硬路由"])
        self.assertTrue(核["valid"], 核["问题"])

    def test_03_乙按未入码重推_当全新入流并转已入(self):
        self._制造入流冲突()
        self.assertEqual(self.命运行("C1")[0]["命运码"], "未入")

        报 = 装夹具("并发重试乙样例")
        self.assertEqual(报["冲突票数"], 0)
        self.assertEqual(报["入窗"], 2)
        self.assertEqual(报["幂等跳过"], 0)
        # 命运账单调推进：未入 → 已入
        码 = [r["命运码"] for r in self.命运行("C1")]
        self.assertEqual(码, ["未入", "已入"])
        窗键 = self.查("SELECT 窗键 FROM 互动事件 WHERE 事件编号='C1'")[0]["窗键"]
        self.assertTrue(窗键)
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def test_04_已入码重推_幂等跳过不得双加(self):
        装夹具("并发冲突甲样例")  # 无冲突，直接入窗
        窗前 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]

        报 = 装夹具("并发重试乙样例")
        self.assertEqual(报["入窗"], 0)
        self.assertEqual(报["幂等跳过"], 2)
        窗后 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]
        self.assertEqual(
            (窗后["点赞合计"], 窗后["送礼合计"], 窗后["事件笔数"], 窗后["版本"]),
            (窗前["点赞合计"], 窗前["送礼合计"], 窗前["事件笔数"], 窗前["版本"]),
        )
        self.assertTrue(贯验.跑贯验()["是否通过"])

    # ---------- 旁路码硬路由 ----------
    def test_05_旁路码只许旁路入口_重推不得入窗(self):
        装夹具("准时热度样例")
        报 = 装夹具("超迟到旁路样例")
        self.assertEqual(报["旁路"], 1)
        self.assertEqual(self.命运行("X1")[0]["命运码"], "旁路")
        窗前 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]

        # 重推 X1：只许旁路入口（旁路-幂等跳过），不得入窗
        报2 = 装载.装一批({
            "批次编号": "批-旁路重推",
            "事件": [{
                "事件编号": "X1", "房间键": "房A", "事件类型": "点赞",
                "事件时": "2026-11-22T12:00:20Z", "处理时": "2026-11-22T12:02:30Z",
                "观众键": "观太晚", "点赞增量": 9,
            }],
        })
        self.assertEqual(报2["入窗"], 0)
        窗后 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]
        self.assertEqual(窗后["点赞合计"], 窗前["点赞合计"])
        x1 = self.查("SELECT 命运, 窗键 FROM 互动事件 WHERE 事件编号='X1'")[0]
        self.assertEqual((x1["命运"], x1["窗键"]), ("已旁路", ""))
        self.assertTrue(贯验.跑贯验()["是否通过"])

    # ---------- 冲突与旁路并发：不得关闭态与合计分叉 ----------
    def test_06_冲突与旁路并发_关闭态与合计不分叉(self):
        def 旁路线程():
            报 = 装夹具("超迟到旁路样例")
            果.append(报)

        果 = []
        进票, 放行 = threading.Event(), threading.Event()
        并发.持票回调["房A"] = lambda 囊: (进票.set(), 放行.wait(timeout=10))

        def 卡票():
            热度窗.扫描关窗("房A", "卡票")

        t = threading.Thread(target=卡票)
        t.start()
        self.assertTrue(进票.wait(5))

        th = threading.Thread(target=旁路线程)
        th.start()
        # 入流撞票 → 整票回滚（含本应旁路的事件），冲突命运=未入
        t2 = threading.Thread(target=lambda: 果.append(装夹具("并发冲突甲样例")))
        t2.start()
        放行.set()
        t.join(5)
        t2.join(5)
        th.join(5)

        # 关闭态与合计互证
        self.assertTrue(贯验.跑贯验()["是否通过"])
        览 = 并发只读.并发总览("房A")
        房 = 览["房间"][0]
        self.assertEqual(房["valid"], 1, 房["问题"])
        # 每个已关闭窗：窗合计 == 窗内已入窗事件合计（并发只读自带此校验）
        for wk, w in 房["关闭态"].items():
            agg = self.查(
                "SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送, COUNT(*) AS n"
                " FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'", (wk,)
            )[0]
            self.assertEqual((agg["赞"], agg["送"], agg["n"]),
                             (w["点赞合计"], w["送礼合计"], w["事件笔数"]))
        # 冲突票的事件为未入码，且未进窗
        for 编号 in ("C1", "C2"):
            码 = self.命运行(编号)[-1]["命运码"] if self.命运行(编号) else None
            if 码 == "未入":
                self.assertEqual(
                    self.查("SELECT COUNT(*) AS c FROM 互动事件 WHERE 事件编号=? AND 命运='已入窗'", (编号,))[0]["c"], 0
                )

    # ---------- 三禁止自检（故意造脏） ----------
    def test_07_禁止_未入却有窗内贡献(self):
        装夹具("准时热度样例")  # E1 已入窗
        conn = 连接()
        try:
            命运账.记账(conn, 房间键="房A", 事件编号="E1", 命运码="未入", 冲突世代=99)
            conn.commit()
            核 = 命运账.查命运(conn, "E1")
        finally:
            conn.close()
        self.assertFalse(核["valid"])
        self.assertFalse(核["三禁止"]["禁止_未入却有窗内贡献"])
        self.assertTrue(any("E1" in x and "未入" in x for x in 核["问题"]))
        self.assertFalse(贯验.跑贯验()["是否通过"])

    def test_08_禁止_已入却不在窗也不在旁路(self):
        装夹具("准时热度样例")
        conn = 连接()
        try:
            命运账.记账(conn, 房间键="房A", 事件编号="E2", 命运码="已入", 冲突世代=5)
            conn.execute("DELETE FROM 互动事件 WHERE 事件编号='E2'")
            # 该窗仍留 E2 贡献，构造「已入但不在窗」互证失败
            核 = 命运账.查命运(conn, "E2")
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(核["valid"])
        self.assertFalse(核["三禁止"]["禁止_已入却不在窗也不在旁路"])

    def test_09_禁止_冲突处置成功命运账空(self):
        # 直接构造一张「有效入流票」但无命运账 → 核证必须报禁止
        账 = 台账连接()
        try:
            账.execute(
                """INSERT INTO 关窗并发账
                   (票号,房间键,动作,批次编号,票时刻,是否有效,冲突次数,冲突来源,事件命运,事件明细,
                    入窗数,旁路数,关窗数,重试自票号)
                   VALUES ('票-入流-房A-空账','房A','入流','批-x','2026-11-22T12:00:00Z',1,0,'','','[]',0,0,0,'')"""
            )
            账.commit()
        finally:
            账.close()
        conn = 连接()
        try:
            核 = 命运账.查命运(conn)
        finally:
            conn.close()
        self.assertFalse(核["valid"])
        self.assertFalse(核["三禁止"]["禁止_冲突成功命运账空"])
        self.assertTrue(any("命运账空" in x for x in 核["问题"]))

    # ---------- 只读核证接口与并发只读协同 ----------
    def test_10_核证接口与并发只读协同(self):
        self._制造入流冲突()
        装夹具("并发重试乙样例")
        核 = 命运账.查命运(连接(), "C1")
        self.assertTrue(核["valid"])
        self.assertTrue(核["硬路由"])
        self.assertEqual(核["命运账"][0]["命运码"], "已入")
        览 = 并发只读.并发总览("房A")
        房 = 览["房间"][0]
        self.assertIn("C1", 房["命运码现场"])
        self.assertEqual(房["命运码现场"]["C1"]["命运码"], "已入")
        self.assertEqual(房["valid"], 1, 房["问题"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""关窗并发半截 · 架构甲（冲突整票回滚）测试。

覆盖题面夹具：
- 并发推流与关窗：最终不得关闭态与合计分叉
- 甲冲突批事件可整票不入；乙重试后事件须入窗或合法旁路可查
- 不得双关闭处理互相覆盖成脏合计
- 只读接口字段：房间、冲突次数、事件命运、关闭态、valid
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="livewave测试-")
os.environ["数据目录"] = _tmp
os.environ["夹具目录"] = str(Path(__file__).resolve().parent.parent.parent / "fixtures")

from app.db import 连接, 台账连接
from app import seed
from app.services import 装载, 热度窗, 并发, 并发只读, 贯验


def 重置库():
    seed.初始化库()
    并发.持票回调.clear()


class 关窗并发试验(unittest.TestCase):
    def setUp(self):
        
        self.目录 = tempfile.mkdtemp(prefix="livewave用例-")
        os.environ["数据目录"] = self.目录
        重置库()
        
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

    def 查账(self, sql, args=()):
        conn = 台账连接()
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def test_01_主链基线_准时迟到旁路仍正确(self):
        r1 = 装载.装一批({
            "批次编号": "批-准时",
            "事件": [
                {"事件编号": "E1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:00:11Z",
                 "观众键": "观1", "点赞增量": 3},
                {"事件编号": "E2", "房间键": "房A", "事件类型": "送礼",
                 "事件时": "2026-11-22T12:00:40Z", "处理时": "2026-11-22T12:00:41Z",
                 "观众键": "观2", "送礼额": 50},
                {"事件编号": "E3", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:06Z",
                 "观众键": "观1", "点赞增量": 2},
            ],
        })
        self.assertEqual(r1["冲突票数"], 0)
        self.assertEqual(r1["入窗"], 3)
        self.assertEqual(r1["新关窗"], 1)
        w1 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]
        self.assertEqual(w1["状态"], "已关闭")
        self.assertEqual((w1["点赞合计"], w1["送礼合计"]), (3, 50))

        
        r2 = 装载.装一批({
            "批次编号": "批-迟到",
            "事件": [
                {"事件编号": "L1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:55Z", "处理时": "2026-11-22T12:01:20Z",
                 "观众键": "观晚", "点赞增量": 4},
            ],
        })
        self.assertEqual(r2["撤回"], 1)
        w1 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]
        
        self.assertEqual((w1["点赞合计"], w1["事件笔数"], w1["版本"]), (7, 3, 3))

        
        r3 = 装载.装一批({
            "批次编号": "批-超迟",
            "事件": [
                {"事件编号": "E4", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:01:50Z", "处理时": "2026-11-22T12:01:51Z",
                 "观众键": "观3", "点赞增量": 5},
                {"事件编号": "E5", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:02:10Z", "处理时": "2026-11-22T12:02:11Z",
                 "观众键": "观4", "点赞增量": 1},
                {"事件编号": "X1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:20Z", "处理时": "2026-11-22T12:02:15Z",
                 "观众键": "观太晚", "点赞增量": 9},
            ],
        })
        self.assertEqual(r3["旁路"], 1)
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def _栅栏(self):
        """新建一对事件，避免跨用例复用已 set 的 event 造成永久阻塞。"""
        return threading.Event(), threading.Event()

    def _挂持票钩(self, 房):
        进票, 放行 = self._栅栏()
        并发.持票回调[房] = lambda 囊: (进票.set(), 放行.wait(timeout=10))
        return 进票, 放行

    def test_02_入流与关窗扫描冲突_整票回滚无半截(self):
        进票, 放行 = self._挂持票钩("房A")
        果 = {}

        def 入流():
            果["报"] = 装载.装一批({
                "批次编号": "批-并发冲突甲",
                "事件": [
                    {"事件编号": "C1", "房间键": "房A", "事件类型": "点赞",
                     "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:00:11Z",
                     "观众键": "观冲1", "点赞增量": 7},
                    {"事件编号": "C2", "房间键": "房A", "事件类型": "送礼",
                     "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:06Z",
                     "观众键": "观冲2", "送礼额": 80},
                ],
            })

        t = threading.Thread(target=入流)
        t.start()
        self.assertTrue(进票.wait(5), "入流票未在预期内取得")

        
        扫 = 热度窗.扫描关窗("房A", "批-扫描撞票")
        放行.set()
        t.join(timeout=5)

        self.assertEqual(扫["冲突"], 1)
        self.assertFalse(扫["明细"][0]["有效"])
        self.assertEqual(果["报"]["冲突票数"], 0)  

        
        self.assertEqual(果["报"]["入窗"], 2)
        self.assertEqual(果["报"]["新关窗"], 1)
        self.assertEqual(
            self.查("SELECT 事件时水位 FROM 水位线 WHERE 房间键='房A'")[0]["事件时水位"],
            "2026-11-22T12:01:05Z",
        )
        w1 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]
        self.assertEqual((w1["状态"], w1["点赞合计"], w1["送礼合计"], w1["事件笔数"]),
                         ("已关闭", 7, 0, 1))
        
        票 = self.查账("SELECT * FROM 关窗并发账 WHERE 动作='关窗扫描'")
        self.assertEqual(len(票), 1)
        self.assertEqual(票[0]["是否有效"], 0)
        self.assertEqual(票[0]["关窗数"], 0)
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def _制造入流冲突(self):
        """关窗扫描持票卡住 → 同房入流抢票失败整票回滚。返回入流报告与持票线程。"""
        进票, 放行 = self._挂持票钩("房A")
        果 = {}

        def 扫描():
            果["扫"] = 热度窗.扫描关窗("房A", "批-扫描持票")

        t = threading.Thread(target=扫描)
        t.start()
        self.assertTrue(进票.wait(5))
        报 = 装载.装一批({
            "批次编号": "批-并发冲突甲",
            "事件": [
                {"事件编号": "C1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:00:11Z",
                 "观众键": "观冲1", "点赞增量": 7},
                {"事件编号": "C2", "房间键": "房A", "事件类型": "送礼",
                 "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:06Z",
                 "观众键": "观冲2", "送礼额": 80},
            ],
        })
        放行.set()
        t.join(5)
        return 报

    def test_03_冲突整票回滚_事件不入水位不动(self):
        """反向撞票：关窗扫描持票，入流抢不到 → 入流整票回滚（架构甲标志性结论）。"""
        报 = self._制造入流冲突()

        
        self.assertEqual(报["冲突票数"], 1)
        self.assertEqual(报["入窗"], 0)
        self.assertEqual(报["入流"], 0)
        self.assertEqual(
            self.查("SELECT COUNT(*) AS c FROM 互动事件 WHERE 事件编号 IN ('C1','C2')")[0]["c"], 0,
        )
        
        self.assertEqual(
            self.查("SELECT 事件时水位 FROM 水位线 WHERE 房间键='房A'")[0]["事件时水位"],
            "2026-11-22T12:00:00Z",
        )
        self.assertEqual(self.查("SELECT COUNT(*) AS c FROM 热度窗")[0]["c"], 0)
        
        坏票 = self.查账("SELECT * FROM 关窗并发账 WHERE 是否有效=0 AND 动作='入流'")
        self.assertEqual(len(坏票), 1)
        self.assertIn("C1", 坏票[0]["事件明细"])
        self.assertIn("C2", 坏票[0]["事件明细"])
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def test_04_乙批重试_冲突后整票重投须入窗或合法旁路(self):
        
        甲 = self._制造入流冲突()
        self.assertEqual(甲["冲突票数"], 1)

        
        报 = 装载.装一批({
            "批次编号": "批-并发重试乙",
            "事件": [
                {"事件编号": "C1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:01:30Z",
                 "观众键": "观冲1", "点赞增量": 7},
                {"事件编号": "C2", "房间键": "房A", "事件类型": "送礼",
                 "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:31Z",
                 "观众键": "观冲2", "送礼额": 80},
            ],
        })
        self.assertEqual(报["冲突票数"], 0)
        self.assertEqual(报["入窗"], 2)
        落 = self.查("SELECT 事件编号, 命运, 窗键 FROM 互动事件 WHERE 事件编号 IN ('C1','C2') ORDER BY 事件编号")
        self.assertEqual([r["命运"] for r in 落], ["已入窗", "已入窗"])
        self.assertTrue(all(r["窗键"] for r in 落))

        
        报2 = 装载.装一批({
            "批次编号": "批-并发重试乙-重",
            "事件": [
                {"事件编号": "C1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:01:40Z",
                 "点赞增量": 7},
            ],
        })
        self.assertEqual(报2["入窗"], 0)
        w1 = self.查("SELECT * FROM 热度窗 WHERE 窗键=?", ("房A|2026-11-22T12:00:00Z",))[0]
        self.assertEqual(w1["点赞合计"], 7)
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def test_05_双关闭不互相覆盖_无脏合计(self):
        
        装载.装一批({
            "批次编号": "批-基",
            "事件": [
                {"事件编号": "B1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:00:11Z",
                 "点赞增量": 3},
                {"事件编号": "B2", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:06Z",
                 "点赞增量": 2},
            ],
        })
        关前 = self.查("SELECT * FROM 热度窗 WHERE 状态='已关闭'")
        self.assertEqual(len(关前), 1)
        版本前 = 关前[0]["版本"]

        
        进票, 放行 = self._挂持票钩("房A")
        果 = {}

        def 慢扫():
            果["慢"] = 热度窗.扫描关窗("房A", "慢扫")

        t = threading.Thread(target=慢扫)
        t.start()
        self.assertTrue(进票.wait(5))
        快 = 热度窗.扫描关窗("房A", "快扫")  
        放行.set()
        t.join(5)

        self.assertEqual(快["冲突"], 1)
        self.assertEqual(果["慢"]["新关窗"], 0)  
        窗 = self.查("SELECT * FROM 热度窗 WHERE 状态='已关闭'")
        self.assertEqual(len(窗), 1)
        self.assertEqual(窗[0]["版本"], 版本前)  
        self.assertEqual((窗[0]["点赞合计"], 窗[0]["事件笔数"]), (3, 1))
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def test_06_只读接口_字段齐全且valid(self):
        
        self.assertEqual(self._制造入流冲突()["冲突票数"], 1)
        装载.装一批({
            "批次编号": "批-并发重试乙",
            "事件": [
                {"事件编号": "C1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:01:30Z",
                 "点赞增量": 7},
                {"事件编号": "C2", "房间键": "房A", "事件类型": "送礼",
                 "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:31Z",
                 "送礼额": 80},
            ],
        })
        览 = 并发只读.并发总览("房A")
        self.assertTrue(览["口径"].startswith("架构甲"))
        房 = next(r for r in 览["房间"] if r["房间键"] == "房A")
        
        self.assertGreaterEqual(房["冲突次数"], 1)
        self.assertEqual(房["valid"], 1)
        self.assertIn("房A|2026-11-22T12:00:00Z", 房["关闭态"])
        命运 = {d["事件编号"]: d for d in 房["事件命运"]}
        self.assertIn("C1", 命运)
        
        self.assertTrue(any("整票回滚" in d["命运"] and d["已入库"] == 0
                            for d in 房["事件命运"] if d["事件编号"] == "C1"))
        self.assertTrue(any(d["已入库"] == 1 and d["命运"] == "已入窗"
                            for d in 房["事件命运"] if d["事件编号"] == "C1"))
        
        self.assertEqual(并发只读.并发总览()["全部valid"], 1)

    def test_07_多房串扰_一房冲突不波及他房(self):
        
        seed.初始化库  
        conn = 连接()
        conn.execute(
            "INSERT INTO 直播间(房间键,标题,主播,开播事件时,状态) VALUES (?,?,?,?,?)",
            ("房B", "隔壁场", "主播小波", "2026-11-22T12:00:00Z", "直播中"),
        )
        conn.execute(
            "INSERT INTO 水位线(房间键,事件时水位,允许迟到秒,更新处理时) VALUES (?,?,?,?)",
            ("房B", "2026-11-22T12:00:00Z", 30, seed.此刻()),
        )
        conn.commit()
        conn.close()

        进票, 放行 = self._挂持票钩("房A")
        果 = {}

        def 卡A():
            果["A"] = 热度窗.扫描关窗("房A", "卡A")

        t = threading.Thread(target=卡A)
        t.start()
        self.assertTrue(进票.wait(5))

        报B = 装载.装一批({
            "批次编号": "批-房B",
            "事件": [
                {"事件编号": "D1", "房间键": "房B", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:01:05Z", "处理时": "2026-11-22T12:01:06Z",
                 "点赞增量": 5},
            ],
        })
        放行.set()
        t.join(5)
        self.assertEqual(报B["冲突票数"], 0)
        self.assertEqual(报B["入窗"], 1)
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def test_08_票内异常_整票回滚留无效票(self):
        
        def 炸(囊):
            raise RuntimeError("票内模拟故障")

        并发.持票回调["房A"] = 炸
        with self.assertRaises(RuntimeError):
            装载.装一批({
                "批次编号": "批-故障",
                "事件": [
                    {"事件编号": "F1", "房间键": "房A", "事件类型": "点赞",
                     "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:00:11Z",
                     "点赞增量": 1},
                ],
            })
        self.assertEqual(
            self.查("SELECT COUNT(*) AS c FROM 互动事件")[0]["c"], 0
        )
        self.assertEqual(
            self.查("SELECT 事件时水位 FROM 水位线 WHERE 房间键='房A'")[0]["事件时水位"],
            "2026-11-22T12:00:00Z",
        )
        坏 = self.查账("SELECT * FROM 关窗并发账 WHERE 是否有效=0")
        self.assertEqual(len(坏), 1)
        self.assertIn("票内模拟故障", 坏[0]["冲突来源"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

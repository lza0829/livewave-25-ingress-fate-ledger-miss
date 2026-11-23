"""跨进程关窗并发：两个独立进程打同一 SQLite 库，验证写保留冲突也整票回滚。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

后端根 = Path(__file__).resolve().parent.parent


def _脚本(数据目录: str, 模式: str) -> str:
    
    return f"""
import os, sys, time, json as j
os.environ["数据目录"] = {json.dumps(数据目录)}
sys.path.insert(0, {json.dumps(str(后端根))})
from app.services import 装载, 热度窗, 并发
if {json.dumps(模式)} == "持票":
    并发.扫描立即写保留 = True
    e = __import__("threading").Event()
    并发.持票回调["房A"] = lambda 囊: e.wait(15)
    print(j.dumps(热度窗.扫描关窗("房A", "跨进程持票")), flush=True)
else:
    并发.抢票超时秒 = 3
    try:
        r = 装载.装一批({{
            "批次编号": "跨进程撞票",
            "事件": [
                {{"事件编号": "P1", "房间键": "房A", "事件类型": "点赞",
                 "事件时": "2026-11-22T12:00:10Z", "处理时": "2026-11-22T12:00:11Z",
                 "点赞增量": 6}},
            ],
        }})
        print(j.dumps(r), flush=True)
    except Exception as ex:
        print(j.dumps({{"异常": str(ex)}}), flush=True)
"""


class 跨进程试验(unittest.TestCase):
    def setUp(self):
        self.目录 = tempfile.mkdtemp(prefix="livewave跨进程-")
        os.environ["数据目录"] = self.目录
        sys.path.insert(0, str(后端根))
        from app import seed
        seed.初始化库()

    def test_同房跨进程_一进程持票另一进程整票回滚(self):
        py = sys.executable
        持 = subprocess.Popen(
            [py, "-I", "-c", _脚本(self.目录, "持票")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=str(后端根),
        )
        
        import time
        time.sleep(1.5)
        撞 = subprocess.run(
            [py, "-I", "-c", _脚本(self.目录, "撞票")],
            capture_output=True, text=True, cwd=str(后端根), timeout=30,
        )
        self.assertEqual(撞.returncode, 0, 撞.stderr)
        报 = json.loads(撞.stdout.strip().splitlines()[-1])
        
        self.assertEqual(报.get("冲突票数"), 1, 报)
        self.assertEqual(报.get("入窗"), 0)

        出, 错 = 持.communicate(timeout=20)
        self.assertEqual(持.returncode, 0, 错)
        扫 = json.loads(出.strip().splitlines()[-1])
        self.assertEqual(扫["冲突"], 0)  

        
        sys.path.insert(0, str(后端根))
        from app.db import 连接, 台账连接
        c = 连接()
        try:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM 互动事件 WHERE 事件编号='P1'").fetchone()[0], 0
            )
            self.assertEqual(
                c.execute("SELECT 事件时水位 FROM 水位线 WHERE 房间键='房A'").fetchone()[0],
                "2026-11-22T12:00:00Z",
            )
        finally:
            c.close()
        a = 台账连接()
        try:
            坏 = a.execute("SELECT COUNT(*) FROM 关窗并发账 WHERE 是否有效=0").fetchone()[0]
            self.assertGreaterEqual(坏, 1)
        finally:
            a.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

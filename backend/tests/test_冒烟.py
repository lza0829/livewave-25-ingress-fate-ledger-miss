"""端到端 HTTP 冒烟：并发关窗只读接口、扫描入口、甲乙夹具全链路。"""
import os
import tempfile
from pathlib import Path

os.environ["数据目录"] = tempfile.mkdtemp(prefix="livewave冒烟-")
os.environ["夹具目录"] = str(Path(__file__).resolve().parent.parent.parent / "fixtures")

from fastapi.testclient import TestClient

from app import seed
from app.main import app

seed.初始化库()
c = TestClient(app)


def 主():
    assert c.get("/api/健康").json()["正常"]

    
    r = c.post("/api/装载/夹具", params={"名": "并发冲突甲样例"})
    assert r.status_code == 200, r.text
    甲 = r.json()
    assert 甲["入窗"] == 2 and 甲["冲突票数"] == 0
    print("甲批:", 甲["入窗"], "入窗,", 甲["冲突票数"], "冲突票")

    # 冲突处置成功必落账：同批事件在入流命运账有 已入 行
    r = c.get("/api/核证/入流命运?事件编号=C1")
    assert r.json()["命运账"][0]["命运码"] == "已入"
    print("入流命运账已落账")

    
    r = c.post("/api/关窗扫描", json={"房间键": "房A"})
    assert r.status_code == 200, r.text
    print("扫描:", r.json())

    
    r = c.get("/api/并发关窗?房间键=房A")
    览 = r.json()
    房 = 览["房间"][0]
    assert {"房间键", "冲突次数", "事件命运", "关闭态", "valid"} <= set(房)
    assert 房["valid"] == 1
    print("只读接口 valid:", 房["valid"], "票历条数:", len(房["票历"]))

    
    r = c.get("/api/并发关窗/台账")
    assert r.status_code == 200 and isinstance(r.json(), list)
    print("台账条数:", len(r.json()))

    
    规 = c.get("/api/站规").json()
    assert "架构甲" in 规["关窗并发口径"]
    print("站规口径:", 规["关窗并发口径"][:24], "...")

    
    r = c.post("/api/装载/夹具", params={"名": "并发重试乙样例"})
    assert r.status_code == 200 and r.json()["入窗"] == 0
    # 乙批重投：已入码幂等跳过，不得双加
    assert r.json()["幂等跳过"] == 2, r.json()


    for 名 in ["准时热度样例", "迟到修正样例", "超迟到旁路样例"]:
        r = c.post(f"/api/装载/夹具?名={名}")
        assert r.status_code == 200, r.text
    assert c.post("/api/贯验").json()["是否通过"]
    print("乙批重投幂等、主链夹具贯验通过")

    # 只读核证接口：命运码硬路由 + 三禁止自检，与并发只读协同
    r = c.get("/api/核证/入流命运?事件编号=C1")
    核 = r.json()
    assert r.status_code == 200 and 核["硬路由"] is True, 核
    assert 核["valid"] is True, 核["问题"]
    assert 核["命运账"][0]["命运码"] == "已入", 核["命运账"][:1]
    assert all(核["三禁止"].values()), 核["三禁止"]
    assert c.get("/api/入流命运核证").status_code == 200
    房 = c.get("/api/并发关窗?房间键=房A").json()["房间"][0]
    assert 房["命运码现场"]["C1"]["命运码"] == "已入", 房["命运码现场"]
    print("命运账核证接口与并发只读协同通过")
    print("HTTP 冒烟全部通过")


if __name__ == "__main__":
    主()

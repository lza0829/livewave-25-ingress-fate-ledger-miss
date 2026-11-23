状态 = "半截"


def 查命运(conn, 事件编号: str | None = None) -> dict:
    rows = []
    try:
        q = "SELECT * FROM 入流命运账"
        args: tuple = ()
        if 事件编号:
            q += " WHERE 事件编号=?"
            args = (事件编号,)
        q += " ORDER BY 编号 DESC LIMIT 50"
        rows = [dict(r) for r in conn.execute(q, args)]
    except Exception:
        rows = []
    return {"命运账": rows, "硬路由": False, "看窗合计现场": True, "valid": True}


def 应幂等跳过(_conn, _事件编号: str) -> bool:
    return False


def 尝试记账(conn, **kwargs) -> None:
    pass

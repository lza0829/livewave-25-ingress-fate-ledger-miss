import React, { useEffect, useState } from "react";
import { 取 } from "../api.js";

const 码色 = { 已入: "通", 旁路: "次", 未入: "败" };

export default function 入流命运() {
  const [览, 设览] = useState(null);
  const [err, 设错] = useState("");
  const [筛, 设筛] = useState("");
  const reload = () => {
    取(`/核证/入流命运${筛 ? `?事件编号=${encodeURIComponent(筛)}` : ""}`)
      .then((r) => { 设览(r); 设错(""); })
      .catch((e) => 设错(String(e.message || e)));
  };
  useEffect(() => { reload(); }, []);
  if (err) return <pre className="码">{err}</pre>;
  if (!览) return <p className="讯">加载中…</p>;

  return (
    <div>
      <h1 className="题">入流命运核证</h1>
      <p className="述">
        命运码<strong>硬路由</strong>：<code>已入</code> 的重推幂等跳过不得双加；
        <code>未入</code> 的重推当全新入流；<code>旁路</code> 只许走旁路入口。
        冲突处置成功同票写入（房间 / 事件编号 / 冲突世代 / 命运码 / 账号）。
      </p>
      <div className="行">
        <input
          className="入"
          placeholder="按事件编号筛（可空）"
          value={筛}
          onChange={(e) => 设筛(e.target.value)}
        />
        <button className="次" onClick={reload}>刷新核证</button>
        <span className="讯">
          valid：<span className={`徽章 ${览.valid ? "通" : "败"}`}>{览.valid ? "通过" : "不通过"}</span>
        </span>
      </div>
      <div className="提示条">
        三禁止自检：
        {Object.entries(览.三禁止).map(([k, v]) => (
          <span key={k} className={`徽章 ${v ? "通" : "败"}`} style={{ margin: "0 6px" }}>
            {v ? "✓" : "✗"} {k}
          </span>
        ))}
      </div>
      {览.问题.length > 0 && (
        <div className="提示条" style={{ color: "#b42318" }}>{览.问题.join("；")}</div>
      )}

      <div className="段">
        <div className="段题">命运账（最近 200 行）</div>
        {!览.命运账.length ? (
          <div className="空态">尚无命运账行。推一批事件或制造一次冲突后自动落账。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th><th>房间</th><th>事件编号</th><th>冲突世代</th>
                <th>命运码</th><th>账号</th><th>窗键</th><th>票号</th><th>时刻</th>
              </tr>
            </thead>
            <tbody>
              {览.命运账.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td><td>{r.房间键}</td><td className="码">{r.事件编号}</td>
                  <td>{r.冲突世代}</td>
                  <td><span className={`徽章 ${码色[r.命运码] || "次"}`}>{r.命运码}</span></td>
                  <td>{r.账号 || "—"}</td><td className="码">{r.窗键 || "—"}</td>
                  <td className="码">{r.票号 || "—"}</td><td className="码">{r.时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

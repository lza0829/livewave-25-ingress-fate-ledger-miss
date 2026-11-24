import React, { useEffect, useState } from "react";
import { 取 } from "../api.js";

export default function 并发关窗页() {
  const [览, 设览] = useState(null);
  const [台账, 设台账] = useState([]);
  const 刷 = async () => {
    设览(await 取("/并发关窗"));
    设台账(await 取("/并发关窗/台账"));
  };
  useEffect(() => { 刷(); }, []);
  if (!览) return <p className="讯">加载中…</p>;

  return (
    <div>
      <h1 className="题">关窗并发 · 架构甲（冲突整票回滚）</h1>
      <p className="述">
        同房入流与关窗扫描抢同一张串行票：关闭态、窗合计、水位、事件命运同票提交；
        抢锁/写保留/窗版本冲突即<strong>整票回滚到冲突前</strong>，事件不入库、水位窗态不动，
        仅在并发台账留无效票，可整票重试。冲突票事件<strong>确定不入窗</strong>，
        与「重试至最终入窗」口径可区分。
      </p>
      <div className="提示条">口径：{览.口径}</div>
      <div className="行">
        <button className="次" onClick={刷}>刷新并发态</button>
        <span className="讯">总冲突次数：{览.总冲突次数} · 全部 valid：
          <span className={`徽章 ${览.全部valid ? "通" : "败"}`}>{览.全部valid ? "是" : "否"}</span>
        </span>
      </div>

      {览.房间.map((房) => (
        <div className="段" key={房.房间键}>
          <div className="段题">
            房间 {房.房间键}
            <span className={`徽章 ${房.valid ? "通" : "败"}`} style={{ marginLeft: 8 }}>
              valid {房.valid}
            </span>
            <span className="讯" style={{ marginLeft: 8 }}>
              票 {房.票数} · 有效 {房.有效票数} · 冲突 {房.冲突次数}
            </span>
          </div>
          {房.问题.length > 0 && (
            <div className="提示条" style={{ color: "#b42318" }}>{房.问题.join("；")}</div>
          )}

          <div className="段题" style={{ marginTop: 10 }}>关闭态与合计（不得分叉）</div>
          <table>
            <thead>
              <tr><th>窗键</th><th>状态</th><th>点赞</th><th>送礼</th><th>笔数</th><th>版本</th></tr>
            </thead>
            <tbody>
              {Object.entries(房.关闭态).map(([wk, w]) => (
                <tr key={wk}>
                  <td className="码">{wk}</td><td>{w.状态}</td>
                  <td>{w.点赞合计}</td><td>{w.送礼合计}</td>
                  <td>{w.事件笔数}</td><td>{w.版本}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="段题" style={{ marginTop: 10 }}>命运码现场（入流命运账·硬路由）</div>
          <table>
            <thead>
              <tr><th>事件编号</th><th>命运码</th><th>冲突世代</th><th>账号</th><th>票号</th></tr>
            </thead>
            <tbody>
              {Object.entries(房.命运码现场 || {}).map(([编号, d]) => (
                <tr key={编号}>
                  <td className="码">{编号}</td>
                  <td>
                    <span className={`徽章 ${d.命运码 === "已入" ? "通" : d.命运码 === "未入" ? "败" : "次"}`}>
                      {d.命运码}
                    </span>
                  </td>
                  <td>{d.冲突世代}</td><td>{d.账号 || "—"}</td>
                  <td className="码">{d.票号 || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="段题" style={{ marginTop: 10 }}>事件命运（冲突回滚 vs 合法入窗可区分）</div>
          <table>
            <thead><tr><th>事件编号</th><th>命运</th><th>已入库</th></tr></thead>
            <tbody>
              {房.事件命运.map((d, i) => (
                <tr key={`${d.事件编号}-${i}`}>
                  <td>{d.事件编号}</td>
                  <td className="码">{d.命运}</td>
                  <td>{d.已入库 ? "是" : "否（整票回滚）"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="段题" style={{ marginTop: 10 }}>票历</div>
          <table>
            <thead>
              <tr><th>票号</th><th>动作</th><th>有效</th><th>冲突来源</th><th>入窗</th><th>旁路</th><th>关窗</th><th>时刻</th></tr>
            </thead>
            <tbody>
              {[...房.票历].reverse().map((p) => (
                <tr key={p.票号}>
                  <td className="码">{p.票号}</td><td>{p.动作}</td>
                  <td><span className={`徽章 ${p.是否有效 ? "通" : "败"}`}>{p.是否有效 ? "有效" : "无效"}</span></td>
                  <td className="码">{p.冲突来源 || "—"}</td>
                  <td>{p.入窗数}</td><td>{p.旁路数}</td><td>{p.关窗数}</td>
                  <td className="码">{p.票时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <div className="段">
        <div className="段题">并发台账（最近 100 票，独立台账库）</div>
        <table>
          <thead>
            <tr><th>票号</th><th>房间</th><th>动作</th><th>有效</th><th>冲突来源</th><th>事件命运</th></tr>
          </thead>
          <tbody>
            {台账.map((p) => (
              <tr key={p.票号}>
                <td className="码">{p.票号}</td><td>{p.房间键}</td><td>{p.动作}</td>
                <td><span className={`徽章 ${p.是否有效 ? "通" : "败"}`}>{p.是否有效 ? "有效" : "无效"}</span></td>
                <td className="码">{p.冲突来源 || "—"}</td>
                <td className="码">{p.事件命运 || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

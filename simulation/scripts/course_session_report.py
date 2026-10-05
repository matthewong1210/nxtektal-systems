"""Offline Chinese dashboard over already-occurred session report evidence.

No simulation, vision, dispatch, network or filesystem calls occur here. HTML
is a disposable saved projection; controls only replay saved data. Reference
labels and future scenario inputs are absent from the embedded UI payload.
"""
from __future__ import annotations

import json
import math
import re


def render_report(report: dict) -> str:
    """Return self-contained HTML; open alongside its relative media/ folder."""
    if not isinstance(report, dict) or report.get("environment") != "SIMULATION":
        raise ValueError("report requires explicit SIMULATION")
    summary = report.get("summary", {})
    current = summary.get("minute")
    if type(current) not in (int, float) or not math.isfinite(current):
        raise ValueError("report requires a finite current simulation minute")

    def past(rows):
        return sorted([row for row in rows if type(row.get("minute")) in (float, int)
                       and math.isfinite(row["minute"]) and row["minute"] <= current],
                      key=lambda row: row["minute"])

    frames = []
    for row in past(report.get("frames", [])):
        path = row.get("image", "")
        safe_path = path if isinstance(path, str) and re.fullmatch(r"media/[A-Za-z0-9_-]+\.png", path) else None
        frames.append({"minute": row["minute"], "frame_id": row.get("frame_id"),
                       "cart_id": row.get("cart_id"), "checkpoint_id": row.get("checkpoint_id"),
                       "image": safe_path, "image_sha256": row.get("image_sha256"),
                       "detection": row.get("detection", {}),
                       "width": row.get("calibration", {}).get("width", 384),
                       "height": row.get("calibration", {}).get("height", 240),
                       "surface": {key: row.get("surface_assumption", {}).get(key) for key in
                                   ("moisture_index", "wetness", "surface_water_mm",
                                    "sand_workability_index", "grass_firmness_index")}})
    frames.sort(key=lambda row: (row["minute"], row["frame_id"] or ""))
    payload = {"summary": summary, "status": report.get("status", "UNKNOWN"),
               "config": {key: report.get("config", {}).get(key) for key in
                          ("days", "advance_minutes", "wall_time_seconds", "disk_limit_mb", "start_date")},
               "catalog": report.get("catalog", []), "frames": frames,
               "weather": past(report.get("weather", [])), "traffic": past(report.get("traffic", [])),
               "timeline": past(report.get("timeline", [])),
               "routes": [{key: row.get(key) for key in ("minute", "cart_id", "x_m", "y_m", "state", "camera_online")}
                          for row in past(report.get("cart_routes", []))],
               "cases": report.get("cases", []), "staff_jobs": report.get("staff_jobs", []),
               "alerts": past(report.get("alerts", [])),
               "actual_shot_count": report.get("actual_shot_count", 0),
               "shots": past(report.get("actual_shots_sample", [])),
               "vision": report.get("vision_evaluation", {})}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return _HTML.replace("__REPORT_DATA__", data)


_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>NXTektal · 全场模拟观察台</title>
<style>
:root{--bg:#0b1515;--panel:#122120;--edge:#293d38;--text:#edf4ed;--muted:#9db0a8;--mint:#9cdb8d;--warn:#e8bc68;--bad:#e77f6d;--blue:#75bccc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:28px}h1,h2,h3,p{margin:0}h1{font-size:28px;letter-spacing:-.8px;font-weight:650}h2{font-size:17px;font-weight:600}h3{font-size:13px;color:var(--muted)}small,.muted{color:var(--muted)}button,select,input{font:inherit}button,select{color:var(--text);background:#1b302b;border:1px solid var(--edge);border-radius:8px;padding:7px 10px}button{cursor:pointer}button:hover{border-color:var(--mint)}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--mint)}header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:18px}.brand{color:var(--mint);font-size:12px;letter-spacing:2px;margin-bottom:7px}.badges{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.badge{display:inline-block;background:#213a2d;color:var(--mint);padding:3px 8px;border-radius:5px;font-size:11px}.badge.warn{color:var(--warn);background:#3a3220}.notice{border-left:3px solid var(--warn);padding:10px 14px;background:#211f19;border-radius:4px;color:#d6cdb7;font-size:12px;margin-bottom:20px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.metric{border:1px solid var(--edge);border-radius:12px;padding:17px 20px;background:var(--panel)}.metric strong{display:block;font-size:31px;line-height:1.4;font-weight:550}.metric small{font-size:11px}.grid{display:grid;grid-template-columns:1.45fr 1fr;gap:16px}.panel{background:var(--panel);border:1px solid var(--edge);border-radius:13px;padding:20px;min-width:0}.panelhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.legend{display:flex;flex-wrap:wrap;gap:13px;font-size:11px;color:var(--muted)}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}.row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.timebar{display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;margin:12px 0}input[type=range]{width:100%;accent-color:var(--mint)}.map{width:100%;height:auto;min-height:180px;display:block;background:#11271f;border:1px solid #2a4034;border-radius:8px}.map text{font-family:inherit}.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.pill{background:#1b302b;border-radius:6px;padding:5px 9px;font-size:12px}.photo{position:relative;width:100%;aspect-ratio:1.6;background:#0b1515;border:1px solid var(--edge);border-radius:8px;overflow:hidden}.photo img{width:100%;height:100%;object-fit:fill}.photo svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.empty{display:flex;align-items:center;justify-content:center;min-height:140px;color:var(--muted);font-size:13px}.photo .empty{position:absolute;inset:0}.photo [hidden]{display:none}.evidence{font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin-top:9px}.subgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.fact{background:#192d27;border-radius:8px;padding:10px 12px;font-size:12px}.fact b{color:var(--text);display:block}.spacer{margin-top:16px}.tablewrap{overflow:auto;max-height:340px}table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;color:var(--muted);font-weight:450;white-space:nowrap;padding:8px;border-bottom:1px solid var(--edge)}td{padding:9px 8px;border-bottom:1px solid #243831;vertical-align:top}.chart{width:100%;background:#102019;border:1px solid var(--edge);border-radius:8px}.linecaption{font-size:11px;color:var(--muted);margin-top:8px}.tasklist{max-height:335px;overflow:auto}.task{padding:11px 0;border-bottom:1px solid var(--edge)}.task strong{font-size:13px}.task small{display:block}.status{font-size:11px;border-radius:4px;padding:2px 5px;background:#263c31;color:var(--mint)}.status.wait{color:var(--warn);background:#3a3220}.progress{height:7px;background:#293b32;border-radius:7px;overflow:hidden;margin:11px 0}.progress span{display:block;height:100%;background:var(--mint);width:0}.footer{margin-top:20px;color:var(--muted);font-size:11px}.full{grid-column:1/-1}.trace{max-height:200px;overflow:auto;font-size:12px}.trace div{padding:6px 0;border-bottom:1px solid var(--edge)}.chart text{font:10px sans-serif;fill:#9db0a8}.sectionnote{font-size:12px;color:var(--muted);margin-top:8px}.currenttag{font-size:11px;color:var(--mint)}@media(max-width:1000px){.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){main{padding:16px}header{display:block}.badges{justify-content:flex-start;margin-top:12px}h1{font-size:24px}.panel{padding:14px}.metric{padding:12px}.metric strong{font-size:25px}.subgrid{grid-template-columns:1fr}.timebar{grid-template-columns:auto 1fr}.timebar output{grid-column:1/-1}}@media print{button,input[type=range]{display:none}}
</style></head><body><main>
<header><div><div class="brand">NXT / COURSE OPERATIONS LAB</div><h1>看见场况，再安排工作</h1><p class="muted">18 洞 · 16 台定位摄像球车 · 练习场与共享人手</p></div><div class="badges"><span class="badge warn">SIMULATION</span><span class="badge">低保真合成图像</span><span class="badge warn">固定规则视觉 · 未训练</span></div></header>
<div class="notice">这是已保存的模拟运行报告。图片来自合成场景，位置和天气均为假设；视觉结果未经真实球场验证。滑动与播放只回放已有记录，不会启动后台或调度真实设备。</div>
<div class="panel"><div class="panelhead"><h2>持续会话</h2><span id="session-status" class="badge"></span></div><div class="row"><span id="session-time"></span><span id="session-open" class="muted"></span></div><div class="progress"><span id="progress"></span></div><div id="budgets" class="muted"></div></div>
<div class="metrics"><div class="metric"><h3>供球端当前可用球</h3><strong id="inventory">—</strong><small>当前模拟账本位置计数</small></div><div class="metric"><h3>实际服务的击球</h3><strong id="served">—</strong><small>已发生的历史累计 · 会重复使用球</small></div><div class="metric"><h3>已保存相机证据</h3><strong id="frame-count">—</strong><small id="frame-usable"></small></div><div class="metric"><h3>共享人手占用</h3><strong id="staff-count">—</strong><small id="staff-queued"></small></div></div>
<div class="grid"><section class="panel"><div class="panelhead"><h2>球场观察与球车轨迹</h2><span class="muted">54 个点位，非全场面积覆盖</span></div><div class="timebar"><button id="play" type="button">播放记录</button><input id="time" type="range" aria-label="模拟历史时间" step="1"><output id="time-label"></output></div><svg id="course-map" class="map" viewBox="0 0 1820 560" role="img" aria-label="18洞检查点与16台球车的历史位置"></svg><div class="legend spacer"><span><i class="dot" style="background:#52665b"></i>未观察</span><span><i class="dot" style="background:#bd9466"></i>过期</span><span><i class="dot" style="background:#9cdb8d"></i>可用画面</span><span><i class="dot" style="background:#e8bc68"></i>检测候选</span><span><i class="dot" style="background:#e77f6d"></i>模糊 / 遮挡</span><span><i class="dot" style="background:#75bccc"></i>球车</span></div><div id="coverage-counts" class="pills"></div><p class="sectionnote">轨迹显示此前 30 分钟的已发生采样。可用画面只代表该点有可判断的图像，不等于没有异常。点击点位可查看证据。</p></section>
<section class="panel"><div class="panelhead"><h2>从像素到巡检证据</h2><select id="photo-picker" aria-label="选择历史相机证据"></select></div><div class="photo"><img id="photo" alt="合成球车视角原始图像" hidden><svg id="boxes" aria-label="固定规则视觉检测框"></svg><div id="photo-empty" class="empty">这一时刻还没有保存的图片</div></div><div id="photo-tags" class="pills"></div><div id="photo-evidence" class="evidence"></div><div class="subgrid"><div class="fact"><b>天气 · 场景假设</b><span id="weather"></span></div><div class="fact"><b>地表状态 · 模型假设</b><span id="soil"></span></div></div><p class="sectionnote">框与分数来自图片像素规则，未训练、未校准。雨量与水分桶模型不能当作实测土壤含水率；沙土工作性是配置假设。</p></section>
<section class="panel"><div class="panelhead"><h2>练习场：现在球在哪里</h2><span class="currenttag">截至报告时刻</span></div><svg id="inventory-chart" class="chart" viewBox="0 0 620 130" role="img" aria-label="供球端历史库存"></svg><p class="linecaption">供球端已记录库存；跨午夜保留同一会话状态。</p><div id="ledger-table" class="tablewrap spacer"></div><p id="ledger-note" class="sectionnote"></p></section>
<section class="panel"><div class="panelhead"><h2>练习场：已经打出的球</h2><span class="currenttag">历史落点样本</span></div><svg id="shots-chart" class="chart" viewBox="0 0 620 255" role="img" aria-label="实际已服务击球的历史合成落点分布"></svg><p id="shots-note" class="sectionnote"></p><p class="sectionnote">这些点是已发生击球的历史终点。同一颗球可能多次出现，也可能已被回收；不能作为当前地面剩余球数。</p></section>
<section class="panel"><div class="panelhead"><h2>机器人与共享人手</h2><span class="currenttag">截至报告时刻</span></div><div id="robots" class="tablewrap"></div><div id="jobs" class="tasklist spacer"></div><p class="sectionnote">机器人负责练习场流程；巡检、补拍及场地处理占用同一模拟人手池。能力和耗时是试验假设，无实机连接。</p></section>
<section class="panel"><div class="panelhead"><h2>问题 → 处理 → 再拍验证</h2><span id="case-count" class="muted"></span></div><div id="cases" class="tasklist"></div><p class="sectionnote">模拟操作员确认产生记录。任务完成仍需之后拍摄的独立可用图像复查；积水巡检不意味着排水成功。</p></section>
<section class="panel full"><div class="panelhead"><h2>运行证据与限制</h2><span class="badge warn">合成场景内评价</span></div><div class="subgrid"><div><p id="vision"></p><p class="sectionnote">按帧比较可见类别是否出现，不是目标框精度、真实球场识别准确率或泛化证明。参考标签仅用于离线评价，不输入检测器和调度策略。</p></div><div><h3>已发生的提醒</h3><div id="alerts" class="trace"></div></div></div></section></div>
<footer class="footer">NXTektal · 离线只读报告。保留 report.html 与同级 media 文件夹即可离线查看；重新加载文件才能看到后台后续发布的进展。</footer></main>
<script id="report-data" type="application/json">__REPORT_DATA__</script><script>
"use strict";
const D=JSON.parse(document.getElementById("report-data").textContent), S=D.summary;
const $=id=>document.getElementById(id), esc=v=>String(v??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const n=(v,d=0)=>typeof v==="number"&&Number.isFinite(v)?v.toLocaleString("zh-CN",{maximumFractionDigits:d}):"—";
const stamp=m=>"第 "+(Math.floor(m/1440)+1)+" 天 "+String(Math.floor(m%1440/60)).padStart(2,"0")+":"+String(Math.floor(m%60)).padStart(2,"0")+" UTC";
const names={DIVOT:"草皮打痕",BUNKER_SURFACE:"沙坑扰动",STANDING_WATER:"疑似积水",DEBRIS:"可见杂物",REPAIR_DIVOT:"修补打痕",RAKE_BUNKER:"整理沙坑",CLEAR_DEBRIS:"清理杂物",INSPECT:"现场巡检",REPHOTOGRAPH:"补拍",USABLE:"可用",BLURRED:"模糊",OCCLUDED:"遮挡",POSE_UNCERTAIN:"定位不确定",CANDIDATE:"待复核",CONFIRMED:"已确认",ASSIGNED:"已安排",IN_PROGRESS:"处理中",AWAITING_VERIFICATION:"待新图复查",VERIFIED:"复查通过",DISMISSED:"已排除",REOPENED:"重新打开",PENDING:"待安排",COMPLETED:"人力作业完成",PARKED:"停驻",DWELLING:"检查点停留",MOVING:"行驶",BUDGET_PAUSED:"本轮预算用尽",SESSION_COMPLETE:"会话完成",PAUSED:"已暂停",RUNNING:"运行中",CHUNK_COMPLETE:"本轮已完成"};
const label=v=>names[v]??String(v??"未知");
names.TIME_BUDGET="本轮时间预算用尽";names.DISK_LIMIT="达到磁盘预算";
const svg=(tag,attrs,parent,text)=>{const e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;if(parent)parent.appendChild(e);return e};
function table(headers,rows){return "<table><thead><tr>"+headers.map(x=>"<th>"+esc(x)+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+r.map(x=>"<td>"+esc(x)+"</td>").join("")+"</tr>").join("")+"</tbody></table>"}
const seenCarts=new Set(D.routes.map(r=>r.cart_id)), latestMinute=Number(S.minute), firstMinute=D.routes.reduce((min,r)=>Math.min(min,r.minute),latestMinute);
$("session-status").textContent=label(D.status);$("session-time").textContent=(D.config.start_date??"")+" 开始 · 已推进到 "+stamp(latestMinute);
$("session-open").textContent=S.facility_open?"练习场开放中":"练习场闭场 · 保留库存、队列及未完成工作";
const progressFraction=Number.isFinite(S.session_progress?.fraction)?S.session_progress.fraction:0;
$("progress").style.width=Math.max(0,Math.min(100,progressFraction*100))+"%";
$("budgets").textContent="计划 "+(D.config.days??"—")+" 天；每次最多推进 "+(D.config.advance_minutes??"—")+" 模拟分钟 / "+(D.config.wall_time_seconds??"—")+" 秒运行预算；磁盘上限 "+(D.config.disk_limit_mb??"—")+" MiB。进度按已保存游标续跑；暂停与预算状态以运行器为准。";
$("inventory").textContent=n(S.inventory);$("served").textContent=n(D.actual_shot_count);$("frame-count").textContent=n(D.frames.length);
$("frame-usable").textContent="其中 "+n(D.frames.filter(f=>f.detection.quality==="USABLE").length)+" 帧通过画质检查";
$("staff-count").textContent=(S.staff?.[1]??"—")+" / "+(S.staff?.[0]??"—");$("staff-queued").textContent="占用 / 总人数 · 等待 "+(S.staff?.[2]??"—");
const time=$("time");time.min=firstMinute;time.max=latestMinute;time.value=latestMinute;
const routes=new Map();for(const r of D.routes){if(!routes.has(r.cart_id))routes.set(r.cart_id,[]);routes.get(r.cart_id).push(r)}
for(const rows of routes.values())rows.sort((a,b)=>a.minute-b.minute);
const framesByPoint=new Map();for(const f of D.frames){if(!framesByPoint.has(f.checkpoint_id))framesByPoint.set(f.checkpoint_id,[]);framesByPoint.get(f.checkpoint_id).push(f)}
const lastAt=(rows,t)=>{let lo=0,hi=rows.length-1,result=null;while(lo<=hi){const mid=(lo+hi)>>1;if(rows[mid].minute<=t){result=rows[mid];lo=mid+1}else hi=mid-1}return result};
const map=$("course-map"), drawY=y=>550-y;
let selectedFrame=null, visibleFrames=[], timer=null;
function showPhoto(frame){selectedFrame=frame;const image=$("photo"),boxes=$("boxes");boxes.replaceChildren();
 if(!frame){image.hidden=true;image.removeAttribute("src");$("photo-empty").hidden=false;$("photo-tags").replaceChildren();$("photo-evidence").textContent="未观察区域保持未知。";$("soil").textContent="无对应画面，未提供地表指标。";return}
 image.hidden=!frame.image;$("photo-empty").hidden=!!frame.image;if(frame.image)image.src=frame.image;else image.removeAttribute("src");
 image.parentElement.style.aspectRatio=(Number(frame.width)||384)+" / "+(Number(frame.height)||240);
 boxes.setAttribute("viewBox","0 0 "+(Number(frame.width)||384)+" "+(Number(frame.height)||240));
 for(const detection of frame.detection.detections??[]){const b=detection.bbox;if(!Array.isArray(b)||b.length!==4||!b.every(Number.isFinite))continue;
  svg("rect",{x:b[0],y:b[1],width:b[2]-b[0],height:b[3]-b[1],fill:"none",stroke:"#ffe179","stroke-width":1.5},boxes);
  svg("text",{x:b[0],y:Math.max(10,b[1]-3),fill:"#fff2b6","font-size":10,"paint-order":"stroke",stroke:"#152319","stroke-width":2},boxes,label(detection.condition)+" "+n(detection.score,2))}
 $("photo-tags").innerHTML=[frame.cart_id,frame.checkpoint_id,label(frame.detection.quality),stamp(frame.minute)].map(x=>'<span class="pill">'+esc(x)+"</span>").join("");
 $("photo-evidence").textContent=frame.frame_id+" · SHA-256 "+(frame.image_sha256??"").slice(0,24)+"… · 未校准分数";
 const soil=frame.surface??{};$("soil").textContent="含水指数 "+n(soil.moisture_index??soil.wetness,2)+" · 表面积水 "+n(soil.surface_water_mm,2)+" mm · "+(soil.sand_workability_index!=null?"沙土工作性 "+n(soil.sand_workability_index,2):"草地硬度指数 "+n(soil.grass_firmness_index,2));
 const picker=$("photo-picker");if(![...picker.options].some(o=>o.value===frame.frame_id)){const option=document.createElement("option");option.value=frame.frame_id;option.textContent=stamp(frame.minute)+" · "+frame.cart_id+" · "+frame.checkpoint_id;picker.appendChild(option)}picker.value=frame.frame_id;
}
function redraw(){const t=Number(time.value);$("time-label").textContent=stamp(t);map.replaceChildren();
 const counts={UNOBSERVED:0,STALE:0,USABLE:0,CANDIDATE:0,UNUSABLE:0};
 for(let h=1;h<=18;h++){const cp=D.catalog.find(p=>p.hole_number===h&&p.surface_type==="fairway");if(!cp)continue;const x=cp.x_m-120,y=cp.y_m-80;
 svg("rect",{x,y:drawY(y+160),width:280,height:160,rx:15,fill:"#193a29",stroke:"#2c5136","stroke-width":2},map);svg("text",{x:x+12,y:drawY(y+160)+24,fill:"#a6bb9c","font-size":17},map,"H"+String(h).padStart(2,"0"))}
 for(const[cart,rows]of routes){const current=lastAt(rows,t);if(!current)continue;const tail=rows.filter(r=>r.minute<=t&&r.minute>=t-30);if(tail.length>1)svg("polyline",{points:tail.map(r=>r.x_m+","+drawY(r.y_m)).join(" "),fill:"none",stroke:"#75bccc","stroke-width":2,opacity:.30},map);
 const g=svg("g",{},map);svg("circle",{cx:current.x_m,cy:drawY(current.y_m),r:8,fill:current.camera_online===false?"#e77f6d":"#75bccc",stroke:"#0b1515","stroke-width":2},g);svg("text",{x:current.x_m+10,y:drawY(current.y_m)-6,fill:"#c3e6ed","font-size":11},g,String(cart).replace("CART-",""));svg("title",{},g,cart+" · "+label(current.state)+" · "+(current.camera_online===true?"相机在线":current.camera_online===false?"相机离线":"相机连接状态未提供"))}
 for(const cp of D.catalog){const frame=lastAt(framesByPoint.get(cp.id)??[],t);const state=!frame?"UNOBSERVED":t-frame.minute>90?"STALE":frame.detection.quality!=="USABLE"?"UNUSABLE":frame.detection.detections?.length?"CANDIDATE":"USABLE";counts[state]++;
 const color={UNOBSERVED:"#52665b",STALE:"#bd9466",UNUSABLE:"#e77f6d",CANDIDATE:"#e8bc68",USABLE:"#9cdb8d"}[state];const dot=svg("circle",{cx:cp.x_m,cy:drawY(cp.y_m),r:7,fill:color,stroke:"#0d2117","stroke-width":2,tabindex:0,role:"button","aria-label":cp.id+" "+state},map);svg("title",{},dot,cp.id+" · "+state);const select=()=>showPhoto(frame);dot.addEventListener("click",select);dot.addEventListener("keydown",e=>{if(e.key==="Enter")select()})}
 $("coverage-counts").innerHTML=[["未观察",counts.UNOBSERVED],["过期",counts.STALE],["可用",counts.USABLE],["候选",counts.CANDIDATE],["需补拍",counts.UNUSABLE],["已记录球车",seenCarts.size]].map(a=>'<span class="pill">'+esc(a[0])+" "+a[1]+"</span>").join("");
 visibleFrames=D.frames.filter(f=>f.minute<=t);const picker=$("photo-picker");picker.replaceChildren();for(const frame of visibleFrames.slice(-200).reverse()){const option=document.createElement("option");option.value=frame.frame_id;option.textContent=stamp(frame.minute)+" · "+frame.cart_id+" · "+frame.checkpoint_id;picker.appendChild(option)}
 if(!visibleFrames.length)showPhoto(null);else showPhoto(selectedFrame&&selectedFrame.minute<=t?selectedFrame:visibleFrames.at(-1));
 const weather=lastAt(D.weather,t),traffic=lastAt(D.traffic,t);$("weather").textContent=weather?"雨 "+n(weather.rain_mm_h,1)+" mm/h · 风 "+n(weather.wind_mps,1)+" m/s · "+n(weather.temperature_c,1)+" °C · 在场 "+n(traffic?.active_visitors)+" 人"+(weather.play_paused?" · 大风暂停":""):"该时刻无天气记录";
}
$("photo-picker").addEventListener("change",e=>showPhoto(visibleFrames.find(f=>f.frame_id===e.target.value)));time.addEventListener("input",()=>{selectedFrame=null;redraw()});
$("play").addEventListener("click",()=>{if(timer){clearInterval(timer);timer=null;$("play").textContent="播放记录";return}if(Number(time.value)>=latestMinute)time.value=firstMinute;$("play").textContent="暂停回放";timer=setInterval(()=>{time.value=Math.min(latestMinute,Number(time.value)+5);selectedFrame=null;redraw();if(Number(time.value)>=latestMinute){clearInterval(timer);timer=null;$("play").textContent="播放记录"}},400)});
function inventoryChart(){const c=$("inventory-chart"),rows=[...D.timeline,S].filter(r=>Number.isFinite(r.inventory));if(!rows.length)return;const max=Math.max(1,...rows.map(r=>r.inventory)),left=Math.min(...rows.map(r=>r.minute)),span=Math.max(1,latestMinute-left);for(let i=0;i<3;i++){const y=20+i*42;svg("line",{x1:45,y1:y,x2:606,y2:y,stroke:"#304437"},c);svg("text",{x:4,y:y+4},c,n(max*(1-i/2)))}svg("polyline",{points:rows.map(r=>(45+(r.minute-left)/span*560)+","+(104-r.inventory/max*84)).join(" "),fill:"none",stroke:"#9cdb8d","stroke-width":2},c);svg("text",{x:45,y:123},c,stamp(left));svg("text",{x:470,y:123},c,stamp(latestMinute))}
inventoryChart();const ledger=S.ledger&&typeof S.ledger==="object"?Object.entries(S.ledger).map(([key,val])=>[key,n(val)]):[["供球端",n(S.inventory)],...(S.zones??[]).map(z=>[z.zone_id,n(z.balls)])];$("ledger-table").innerHTML=table(["账本位置 / 区域","当前球数"],ledger);$("ledger-note").textContent=S.ledger?"当前模拟账本的完整位置计数；历史落点不参与此表计算。":"此报告展示供球端和场地区域的账本投影，未包含完整账本位置；不把这些行相加当作全场总球数。";
function shotChart(){const c=$("shots-chart"),shots=D.shots.filter(s=>Number.isFinite(s.landing_x_m)&&Number.isFinite(s.landing_y_m));const xmax=Math.max(300,...shots.map(s=>s.landing_x_m)),ymax=Math.max(80,...shots.map(s=>Math.abs(s.landing_y_m)));for(let m=0;m<=300;m+=100){const x=42+m/xmax*560;svg("line",{x1:x,y1:15,x2:x,y2:225,stroke:"#294535"},c);svg("text",{x:x-8,y:243},c,m+"m")}svg("line",{x1:42,y1:120,x2:602,y2:120,stroke:"#42664a","stroke-dasharray":"4 5"},c);for(const s of shots)svg("circle",{cx:42+s.landing_x_m/xmax*560,cy:120-s.landing_y_m/ymax*99,r:1.4,fill:"#d5e6a5",opacity:.40},c);svg("text",{x:8,y:124},c,"打位");$("shots-note").textContent="实际服务 "+n(D.actual_shot_count)+" 次；展示 "+n(shots.length)+" 个历史终点样本，距离、球杆和天气响应均为合成假设。"}
shotChart();$("robots").innerHTML=table(["机器人","活动 / 状态","电量","载球","目标"],(S.robots??[]).map(r=>[r.robot_id,String(r.activity)+" · "+String(r.health),Number.isFinite(r.battery_frac)?n(r.battery_frac*100,1)+"%":"—",n(r.payload_balls)+" / "+n(r.payload_capacity_balls),r.destination??r.assigned_zone??r.location]));
const jobs=[...D.staff_jobs].sort((a,b)=>(b.available_minute??0)-(a.available_minute??0));$("jobs").innerHTML=jobs.length?jobs.slice(0,80).map(j=>'<div class="task"><strong>'+esc(label(j.task_kind))+" · "+esc(j.checkpoint_id)+'</strong> <span class="status '+(j.status==="PENDING"?"wait":"")+'">'+esc(label(j.status))+"</span><small>"+esc(stamp(j.available_minute))+" 发现 · 期限 "+esc(stamp(j.deadline_minute))+" · 预计占用 "+esc(n(j.duration_minutes))+" 分钟</small></div>").join(""):'<p class="empty">还没有由观察释放的人员任务</p>';
$("case-count").textContent=D.cases.length+" 个历史问题记录";const cases=[...D.cases].sort((a,b)=>String(b.opened_at_utc).localeCompare(String(a.opened_at_utc)));$("cases").innerHTML=cases.length?cases.slice(0,80).map(c=>'<div class="task"><strong>'+esc(label(c.condition_kind))+" · H"+esc(c.hole_number)+" / "+esc(c.checkpoint_id)+'</strong> <span class="status '+(c.status==="AWAITING_VERIFICATION"?"wait":"")+'">'+esc(label(c.status))+"</span><small>"+esc(c.opened_at_utc)+" · "+esc((c.observation_ids??[]).length)+" 条观察 · "+esc((c.task_ids??[]).length)+" 次作业</small></div>").join(""):'<p class="empty">暂无由可用图像发现的问题记录</p>';
const v=D.vision;$("vision").textContent="参考可见类别 "+n(v.visible_reference_classes)+"，预测类别 "+n(v.predicted_classes)+"，匹配 "+n(v.matching_classes)+"。"+(v.precision!=null?"合成类别精确率 "+n(v.precision*100,1)+"%；":"暂无可计算精确率；")+(v.recall!=null?"召回率 "+n(v.recall*100,1)+"%。":"暂无可计算召回率。");
$("alerts").innerHTML=D.alerts.length?D.alerts.slice(-30).reverse().map(a=>"<div>"+esc(stamp(a.minute))+" · "+esc(a.kind)+" · "+esc(a.cart_id??a.checkpoint_id??"")+"</div>").join(""):'<p class="muted">暂无提醒记录</p>';
redraw();
</script></body></html>'''

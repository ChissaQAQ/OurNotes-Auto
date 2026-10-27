"""统计与成就的两个页面：「统计与成就」窗口里的 ``data/stats.html``，MXU 设置页「统计与成就」卡片里的
``data/stats_mxu.md``。数据见 :mod:`.stats`。

窗口里的页面分五个页签（概览、成就、演奏数据、谱面、运行记录），只用内联的 CSS / JS / SVG，不引用外部文件；
打完一局自动重新载入，页签、筛选、搜索、排序和滚动位置都保留。右上角的「备份与恢复」对话框导出、导入记录
（要窗口背后的服务，见 stats_server.py）。
"""

from __future__ import annotations

import html
import json
import math
import time
from collections.abc import Sequence
from datetime import date, timedelta

from .backup import MAX_UPLOAD
from .report import ChartStats, judgements, verdict
from .sources import DIFFICULTIES
from .stats import (
    ACHIEVEMENTS,
    GROUPS,
    PAGE_SIZE,
    TIERS,
    VER_MARK,
    WINDOW_TEXT,
    Achievement,
    Session,
    Totals,
    View,
    _date,
    _diff_rows,
    _facts,
    _pct,
    _when,
    backup_text,
    duration,
    live_key,
    next_goals,
    progress_text,
    recent_unlocked,
    secret_text,
)
from .stats_window import TITLE

# 自动刷新：file:// 下不能 fetch，每 3 秒插一个 <script src="stats_ver.js?…">，读到的版本和页面里的不同才重新载入
# （窗口在后台或正在输入时先等）；从 MXU 设置页卡片的链接打开时（MXU 网页服务的 /api/local-file?path=data/stats.html）
# 同样通过这个接口读 data/stats_ver.js；窗口背后的服务给的页面（地址里有 key）读的时候带上 key。
# 新解锁的成就弹提示（和上次看到的比，第一次打开不弹）。
LIVE_JS = """(()=>{
const ss=(k,v)=>{try{if(v===undefined)return sessionStorage.getItem(k);if(v===null)sessionStorage.removeItem(k);else sessionStorage.setItem(k,v);}catch(e){}return null;};
const y=ss('stats-scroll');
if(y!==null){ss('stats-scroll',null);addEventListener('load',()=>scrollTo(0,+y||0));}
let got=[],seen=null;
try{got=JSON.parse(document.getElementById('stats-unlocked').textContent)}catch(e){}
if(!Array.isArray(got))got=[];
try{seen=JSON.parse(localStorage.getItem('stats-seen'))}catch(e){}
function toast(a){
let box=document.querySelector('.toasts');
if(!box){box=document.createElement('div');box.className='toasts';document.body.appendChild(box);}
const tier=[1,2,3,4].includes(+a[3])?+a[3]:1,t=document.createElement('div');
t.className='toast t'+tier;t.setAttribute('role','status');
t.innerHTML='<span class="to-glow"></span><span class="badge lg t'+tier+'"><svg class="gi"><use></use></svg></span><div class="to-b"><div class="to-k"></div><div class="to-n"></div><div class="to-d"></div></div><button class="to-x" title="关闭">×</button><i class="to-l"></i>';
t.querySelector('use').setAttribute('href','#'+String(a[5]||'g-note').replace(/[^\\w-]/g,''));
t.querySelector('.to-k').textContent='成就解锁 · '+(a[4]||'');
t.querySelector('.to-n').textContent=a[1]||'';
t.querySelector('.to-d').textContent=a[2]||'';
const close=()=>{if(t.classList.contains('out'))return;t.classList.add('out');setTimeout(()=>t.remove(),450);};
t.querySelector('.to-l').addEventListener('animationend',close);
t.onclick=e=>{if(!e.target.closest('.to-x')&&window.statsGoto)statsGoto(a[0]);close();};
box.appendChild(t);
}
const quiet=ss('stats-quiet');ss('stats-quiet',null);  // 刚导入了备份：导入的成就不弹提示
if(Array.isArray(seen)&&!quiet){const s=new Set(seen);got.filter(a=>Array.isArray(a)&&!s.has(a[0])).slice(0,5).forEach((a,i)=>setTimeout(()=>toast(a),400+i*900));}
try{localStorage.setItem('stats-seen',JSON.stringify(got.map(a=>a[0])))}catch(e){}
let pending=false;
function busy(){const e=document.activeElement;return document.hidden||!!(e&&/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName))||!!document.querySelector('.toast:not(.out),dialog[open]');}
function maybe(){if(pending&&!busy()){ss('stats-scroll',String(scrollY));location.reload();}}
window.statsVer=v=>{if(v!==STATS_VER){pending=true;maybe();}};
document.addEventListener('visibilitychange',maybe);
document.addEventListener('focusout',()=>setTimeout(maybe,0));
document.addEventListener('close',()=>setTimeout(maybe,0),true);
const at=new URL(location.href),via=at.searchParams.get('path'),key=at.searchParams.get('key');
function url(name){if(!via)return name+'?'+(key?'key='+encodeURIComponent(key)+'&':'')+Date.now();at.searchParams.set('path',via.slice(0,via.lastIndexOf('/')+1)+name);at.searchParams.set('t',Date.now());return at.href;}
function load(name){const s=document.createElement('script');s.src=url(name);s.onload=s.onerror=()=>s.remove();document.head.appendChild(s);}
const KINDS=LIVE_CFG.kinds,STALE=LIVE_CFG.stale;
function fmt(sec){sec=Math.max(0,sec);if(sec<3570)return Math.round(sec/60)+' 分钟';return (sec/3600).toFixed(1)+' 小时';}
function mmss(sec){sec=Math.max(0,Math.round(sec));return Math.floor(sec/60)+':'+String(sec%60).padStart(2,'0');}
function hhmm(ts){const d=new Date(ts*1000);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
function ago(sec){return sec<60?'刚开始':'已 '+fmt(sec);}
let rows=null;
function paint(){
const now=Date.now()/1000,map=rows?new Map(rows.map(r=>[String(r.key),r])):null;
document.querySelectorAll('.live[data-key]').forEach(el=>{
const row=map?map.get(String(el.dataset.key)):undefined;
if(map&&!row){el.hidden=true;return;}  // 这次运行已经结束（页面刷新前）
el.hidden=false;
const stale=!!row&&now-row.upd>STALE,stop=stale?row.upd:null;
el.classList.toggle('stale',stale);
const start=+el.dataset.start||now,end=stop!==null?Math.min(now,stop):now;
el.querySelectorAll('.el b').forEach(b=>{b.textContent=fmt(end-start);});
const box=el.querySelector('.lnow');
if(!box)return;
box.hidden=!row.kind;  // 刚开始、还没报上来在做什么：和页面生成时一样先不显示这一行
if(!row.kind)return;
const k=KINDS[row.kind]||['进行中','u-play'],u=box.querySelector('use');
if(u)u.setAttribute('href','#'+k[1]);
box.querySelector('.lk').textContent=k[0];
box.querySelector('.ld').textContent=row.detail||'';
const bits=stale?['超过 '+Math.max(1,Math.round((now-row.upd)/60))+' 分钟没有更新，可能已经停止']
:[ago(end-(+row.since||start))];
if(!stale&&row.eta>end)bits.push('下一个 LB '+mmss(row.eta-end)+' 后恢复');
if(!stale&&row.daily>end)bits.push('下次领日常 '+hhmm(row.daily));
box.querySelector('.lx').textContent=bits.join(' · ');
});
}
window.statsLive=r=>{if(Array.isArray(r)){rows=r;paint();}};
setInterval(paint,1000);
document.addEventListener('visibilitychange',paint);
function poll(){load('stats_ver.js');load('stats_live.js');}
poll();
setInterval(poll,3000);
})();"""

TABS = (("ov", "概览", "u-grid"), ("ach", "成就", "u-trophy"), ("data", "演奏数据", "u-chart"), ("charts", "谱面", "u-music"), ("runs", "运行记录", "u-history"))
GROUP_ICON = {"演奏": "g-note", "完美": "g-star", "音符": "g-notes", "谱面": "g-diamond", "乐队": "g-band", "时长": "g-clock", "日常": "g-spark", "趣味": "g-smile"}
WEEK = "一二三四五六日"
UI_KEY = "stats-ui"  # sessionStorage 里存界面状态（页签、筛选、搜索、排序、展开的歌、表格滚动、页码）

# 实时状态：正在运行的任务此刻在做什么（runner 报的 kind → 页面上的名字、图标），见 stats.Tracker.status。
# 不在表里的 kind 当成一件普通的事，只显示说明。
LIVE_KINDS = {
    "select": ("换歌", "u-search"),
    "play": ("演奏中", "u-music"),
    "lb": ("等 LB 恢复", "u-clock"),
    "maintenance": ("等开服", "u-lock"),
    "daily": ("领取日常", "u-spark"),
    "studio": ("领取录音室练习", "u-star"),
    "restart": ("重启游戏", "u-history"),
}
LIVE_STALE_S = 1200  # 这么久没更新（进程被强行结束）就当作已经停止：标出来，时间停在最后一次更新


def live_block(view: View) -> str:
    """页面最后的自动刷新脚本和已解锁的成就（弹解锁提示用；隐藏成就没解锁的不在里面），
    以及实时状态用的 kind 表和多久算已经停止。"""
    got = [[a.id, a.name, a.desc, a.tier, TIERS.get(a.tier, ""), GROUP_ICON.get(a.group, "g-note")] for a in ACHIEVEMENTS if a.id in view.unlocked]
    data = json.dumps(got, ensure_ascii=False).replace("</", "<\\/")
    cfg = json.dumps({"kinds": LIVE_KINDS, "stale": LIVE_STALE_S}, ensure_ascii=False).replace("</", "<\\/")
    return (f'<script id="stats-unlocked" type="application/json">{data}</script>'
            f'<script>const STATS_VER="{VER_MARK}",LIVE_CFG={cfg};{LIVE_JS}</script>')


# ---------------------------------------------------------------- MXU 设置页的卡片
# 卡片是 MXU 选项说明里的一段 HTML：marked 把 <div 开头、中间没有空行的一段当 HTML 块原样留下，
# DOMPurify 保留 style 属性（MXU 没有 CSP）。只用内联样式，颜色只用 MXU 的主题变量（跟着浅色 / 深色和强调色变），
# 不靠 MXU 的 class；样式被去掉时按顺序读也是通顺的文字。不放链接（MXU 只能打开网址）。

# 徽章等级 → (名字, 颜色)。颜色从主题变量调出来，写在卡片最外层，下面用 var(--on-t1) 这样引用
_TIERS = {
    1: ("铜", "color-mix(in srgb,color-mix(in srgb,var(--color-warning),var(--color-error)) 72%,var(--color-text-secondary))"),
    2: ("银", "color-mix(in srgb,var(--color-text-secondary) 60%,var(--color-text-muted))"),
    3: ("金", "var(--color-warning)"),
    4: ("传说", "var(--color-error)"),
}
_DIM = "color:var(--color-text-tertiary)"
_CUT = "white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
_STRONG = "color:var(--color-text-primary);font-weight:600"
_NUM = "font-variant-numeric:tabular-nums"
# 强调色可以选很浅的（珍珠白），进度条和圆点往文字色那边调一点，浅色、深色背景上都看得清
_ACCENT = "color-mix(in srgb,var(--color-accent) 80%,var(--color-text-primary))"
_ICON_WINDOW = '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 4v4"/><path d="M2 8h20"/><path d="M6 4v4"/>'
_ICON_OPEN = '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
_ICON_CLOCK = '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'


def _svg(paths: str, size: int = 14) -> str:
    """线条图标：颜色跟着文字（currentColor），去掉样式也不会变成一块黑。"""
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" style="display:block;flex:none">{paths}</svg>'
    )


def _short_date(ts: float, now: float) -> str:
    """今年的只写月-日。"""
    day = _date(ts)
    return day[5:] if day[:4] == _date(now)[:4] else day


def _mxu_tile(label: str, value: str, unit: str, sub: str, first: bool) -> str:
    edge = "" if first else "border-left:1px solid var(--color-border);"
    unit_html = f' <span style="font-size:12px;font-weight:500;color:var(--color-text-secondary)">{_e(unit)}</span>' if unit else ""
    return (
        f'<div style="{edge}padding:9px 12px 10px;min-width:0">'
        f'<span style="display:block;font-size:11px;{_DIM};{_CUT}">{_e(label)}</span> '
        f'<span style="display:block;margin-top:1px;{_CUT}"><b style="font-size:18px;line-height:24px;{_STRONG};{_NUM}">{_e(value)}</b>{unit_html}</span> '
        f'<span style="display:block;margin-top:1px;font-size:11px;{_DIM};{_CUT}">{_e(sub)}</span></div>'
    )


def _mxu_tiles(view: View) -> str:
    """四个关键数字；还没有记录时换成一句话。"""
    t = view.totals
    # MXU 设了背景图时 --mxu-bg-* 是半透明的面板色（index.css .has-background-image），没设时退回不透明的
    box = "border:1px solid var(--color-border);border-radius:10px;background:var(--mxu-bg-secondary,var(--color-bg-secondary))"
    if not t.plays and not t.runs:
        return f'<div style="{box};border-style:dashed;padding:14px 12px;text-align:center;{_DIM}">还没有演奏记录：打完一局后这里会显示累计数据和成就。</div>'
    run_value, _, run_unit = duration(t.run_s).rpartition(" ")
    run_sub = f"其中挂机 {duration(t.idle_s)}" if t.idle_s else f"{t.runs:,} 次运行" if t.runs else "按演奏记录估算"
    today = t.per_day.get(date.fromtimestamp(view.now), 0)
    tiles = [
        ("累计运行", run_value, run_unit, run_sub),
        ("演奏", f"{t.plays:,}", "局", f"{len(t.per_day):,} 天" + (f" · 今天 {today:,} 局" if today else "")),
        ("ALL PERFECT", f"{t.ap:,}", "局", f"{_pct(t.ap, t.plays)} · FC {_pct(t.fc, t.plays)}"),
        ("AP 过的谱面", f"{t.charts_ap:,}", "张", f"打过 {len(t.charts):,} 张"),
    ]
    cells = "".join(_mxu_tile(*tile, first=i == 0) for i, tile in enumerate(tiles))
    return f'<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));{box};overflow:hidden">{cells}</div>'


def _mxu_badges(view: View) -> str:
    """成就进度：一个成就一格、按分组隔开，解锁的按等级上色；鼠标停在格子上看是哪个（隐藏的只写「???」）。"""
    got = sum(a.id in view.unlocked for a in ACHIEVEMENTS)
    total = len(ACHIEVEMENTS)
    pct = f"{got / total:.0%}" if total else "-"
    tiers = []
    for tier, (name, _) in _TIERS.items():  # 只数解锁了的，不透露隐藏成就的等级
        n = sum(a.tier == tier and a.id in view.unlocked for a in ACHIEVEMENTS)
        dot = f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--on-t{tier});margin-right:4px"></span>'
        tiers.append(f'<span style="display:inline-flex;align-items:center">{dot}{name} {n}</span>')
    groups = []
    for group in GROUPS:
        items = [a for a in ACHIEVEMENTS if a.group == group]
        cells = []
        for a in items:
            when = view.unlocked.get(a.id)
            if when is not None:
                tip, color = f"{a.name}：{a.desc}（{_date(when)} 解锁）", f"var(--on-t{a.tier})"
            elif a.hidden:
                tip, color = f"???：{secret_text(a)}", "var(--color-bg-hover)"
            else:
                tip, color = f"{a.name}：{a.desc}（{progress_text(a, a.value(view.totals))}）", "var(--color-bg-hover)"
            cells.append(f'<span title="{_e(tip)}" style="flex:1;height:6px;border-radius:2px;background:{color}"></span>')
        done = sum(a.id in view.unlocked for a in items)
        groups.append(f'<span title="{_e(f"{group} {done} / {len(items)}")}" style="display:flex;gap:2px;flex:{len(items)} 1 0">{"".join(cells)}</span>')
    head = (
        '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap">'
        f'<span><b style="font-size:13px;{_STRONG}">成就 {got} / {total}</b> <span style="{_DIM};margin-left:4px">{pct}</span></span> '
        f'<span style="display:flex;gap:12px;font-size:11px;{_DIM}">{" ".join(tiers)}</span></div>'
    )
    return f'<div>{head}<div style="display:flex;gap:6px;margin-top:7px">{"".join(groups)}</div></div>'


def _mxu_heading(text: str) -> str:
    return f'<div style="font-size:11px;font-weight:600;{_DIM};margin-bottom:2px">{_e(text)}</div>'


def _mxu_recent(view: View) -> str:
    rows = [_mxu_heading("最近解锁")]
    for a in recent_unlocked(view, 3):
        c = f"var(--on-t{a.tier})"
        badge = (
            f'<span title="{_e(_TIERS.get(a.tier, ("",))[0])}" style="flex:none;width:26px;height:26px;border-radius:50%;'
            f"display:flex;align-items:center;justify-content:center;font:13px 'Segoe UI Symbol','Segoe UI',sans-serif;color:{c};"
            f'background:color-mix(in srgb,{c} 14%,transparent);border:1px solid color-mix(in srgb,{c} 45%,transparent)">{_e(GROUPS.get(a.group, "♪"))}</span>'
        )
        rows.append(
            f'<div style="display:flex;align-items:center;gap:9px;margin-top:8px">{badge} '
            '<span style="flex:1;min-width:0"><span style="display:flex;justify-content:space-between;gap:8px">'
            f'<b style="{_STRONG};{_CUT}">{_e(a.name)}</b> <span style="flex:none;{_DIM};{_NUM}">{_e(_short_date(view.unlocked[a.id], view.now))}</span></span> '
            f'<span style="display:block;font-size:11px;{_DIM};{_CUT}">{_e(a.desc)}</span></span></div>'
        )
    if len(rows) == 1:
        rows.append(f'<div style="margin-top:8px;{_DIM}">还没有解锁的成就。</div>')
    return f'<div style="min-width:0">{"".join(rows)}</div>'


def _mxu_goals(view: View) -> str:
    rows = [_mxu_heading("快要达成" if view.unlocked else "下一个目标")]
    for a in next_goals(view):
        value = a.value(view.totals)
        width = max(0.0, min(1.0, value / a.target)) * 100 if a.target else 0.0
        rows.append(
            '<div style="margin-top:8px"><span style="display:flex;align-items:baseline;gap:6px;min-width:0">'
            f'<b style="flex:none;{_STRONG}">{_e(a.name)}</b> <span style="font-size:11px;{_DIM};{_CUT}">{_e(a.desc)}</span></span> '
            '<span style="display:flex;align-items:center;gap:8px;margin-top:5px">'
            '<span style="flex:1;height:5px;border-radius:3px;background:var(--color-bg-hover);overflow:hidden">'
            f'<span style="display:block;height:100%;width:{width:.1f}%;border-radius:3px;background:{_ACCENT}"></span></span> '
            f'<span style="flex:none;min-width:92px;text-align:right;font-size:11px;color:var(--color-text-secondary);{_NUM}">{_e(progress_text(a, value))}</span></span></div>'
        )
    if len(rows) == 1:
        rows.append(f'<div style="margin-top:8px;{_DIM}">不隐藏的成就都解锁了，剩下的是隐藏成就。</div>')
    return f'<div style="min-width:0">{"".join(rows)}</div>'


def _run_text(s: Session) -> str:
    return f"{s.task or '打歌'} · {duration(s.seconds)} · {s.plays:,} 局，AP {s.ap:,}"


def _mxu_run(view: View) -> str:
    """一行运行情况：正在运行的（截至更新时），没有的话是上一次运行；从来没有运行记录时不显示。"""
    row = "display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:8px"
    if view.live:
        dot = (
            f'<span style="flex:none;width:7px;height:7px;border-radius:50%;background:{_ACCENT};'
            'box-shadow:0 0 0 3px color-mix(in srgb,var(--color-success) 25%,transparent)"></span>'
        )
        text = "；".join(_run_text(s) for s in view.live[:2]) + (f"；还有 {len(view.live) - 2} 个" if len(view.live) > 2 else "")
        return (
            f'<div style="{row};background:color-mix(in srgb,var(--color-success) 9%,transparent)">{dot} '
            f'<span style="min-width:0;{_CUT}"><b style="{_STRONG}">进行中</b> {_e(text)}</span></div>'
        )
    done = [s for s in view.sessions if not any(s is r for r in view.live)]
    if not done:
        return ""
    last = max(done, key=lambda s: s.end)
    return (
        f'<div style="{row};background:var(--mxu-bg-tertiary,var(--color-bg-tertiary))">'
        f'<span style="{_DIM}">{_svg(_ICON_CLOCK, 13)}</span> '
        f'<span style="min-width:0;{_CUT}"><b style="{_STRONG}">上次运行</b> {_e(_when(last.start))} 开始 · {_e(_run_text(last))}</span></div>'
    )


def render_mxu_md(view: View, buttons: Sequence[tuple[str, str]] = ()) -> str:
    """MXU 设置页「统计与成就」卡片的内容（打开设置时读一次）：关键数字、成就进度、最近解锁和快要达成的、
    运行情况、怎么打开窗口。``buttons`` 是「在浏览器里打开」按钮，每个 MXU 可能在用的端口一个
    （见 :func:`.stats.mxu_buttons`），按钮是图片，读不到的不占位置；没有时不放按钮。
    整张卡片是一个 HTML 块，中间不能有空行。"""
    tiers = ";".join(f"--on-t{tier}:{color}" for tier, (_, color) in _TIERS.items())
    cols = f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px 22px">{_mxu_recent(view)} {_mxu_goals(view)}</div>'
    # 按钮之间不能有空白：读不到的按钮是 0×0，空白会留下空隙
    links = "".join(
        f'<a href="{_e(url)}" target="_blank" rel="noopener noreferrer" title="用默认浏览器打开完整的统计与成就页面" '
        f'style="display:inline-grid;line-height:0;border-radius:8px;text-decoration:none"><img src="{_e(image)}" alt="" style="display:block"></a>'
        for url, image in buttons
    )
    box = (
        '<div style="padding:10px 12px;border-radius:10px;border:1px solid color-mix(in srgb,var(--color-accent) 40%,var(--color-border));'
        'background:color-mix(in srgb,var(--color-accent) 9%,transparent)">'
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px">'
        f'<span style="flex:1 1 auto;min-width:0;display:flex;align-items:center;gap:10px"><span style="color:{_ACCENT}">{_svg(_ICON_WINDOW, 18)}</span> '
        f'<b style="{_STRONG}">打开单独的「统计与成就」窗口</b></span> '
        f'<span style="flex:none;display:inline-flex;margin-left:auto">{links}</span></div>'
        '<div style="margin:4px 0 0 28px;font-size:12px;color:var(--color-text-secondary)">'
        f'<span style="display:block">运行任务<b style="{_STRONG};white-space:nowrap">「打开统计窗口」</b>，或者双击包目录里的'
        f'<span style="white-space:nowrap"><b style="{_STRONG}">「统计与成就」</b>快捷方式</span>。</span>'
        f'<span style="display:block;{_DIM}">{_e(WINDOW_TEXT)}。</span></div></div>'
    )
    foot = (
        f'<div style="display:flex;flex-direction:column;gap:6px">{box} '
        f'<div style="display:flex;flex-wrap:wrap;gap:2px 12px;font-size:11px;{_DIM}"><span>{_e(backup_text(view))}</span>'
        f'<span style="margin-left:auto;{_NUM}">更新于 {_e(_when(view.now))}</span></div></div>'
    )
    body = "\n".join(p for p in (_mxu_tiles(view), _mxu_badges(view), cols, _mxu_run(view), foot) if p)
    return f'<div style="{tiers};display:flex;flex-direction:column;gap:12px;margin:4px 0 2px;line-height:1.45">\n{body}\n</div>\n'


def render_mxu_button() -> str:
    """卡片上「在浏览器里打开」按钮的图片（见 :func:`.stats.mxu_buttons`）：颜色固定用页面的主题色，
    浅色、深色设置页上都看得清。"""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="136" height="28" viewBox="0 0 136 28">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#e4508f"/><stop offset="1" stop-color="#7c5cff"/></linearGradient></defs>'
        '<rect width="136" height="28" rx="8" fill="url(#g)"/>'
        '<g transform="translate(13 7.5) scale(.5417)" fill="none" stroke="#fff" stroke-width="2.2" '
        f'stroke-linecap="round" stroke-linejoin="round">{_ICON_OPEN}</g>'
        '<text x="32" y="14" dominant-baseline="central" fill="#fff" font-size="13" font-weight="600" '
        "font-family=\"'Segoe UI','Microsoft YaHei UI','Microsoft YaHei',sans-serif\">在浏览器里打开</text></svg>\n"
    )


# ---------------------------------------------------------------- 图标


def _sym(name: str, body: str) -> str:
    """线条图标（界面用，跟着文字颜色）。"""
    return (f'<symbol id="{name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</symbol>')


SPRITE = (
    '<svg class="sprite" aria-hidden="true"><defs>'
    '<linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff6fb5"/>'
    '<stop offset=".5" stop-color="#a77bff"/><stop offset="1" stop-color="#4fc3ff"/></linearGradient>'
    '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e4508f"/>'
    '<stop offset="1" stop-color="#7c5cff"/></linearGradient></defs>'
    # 徽章上的分组图案（实心）
    '<symbol id="g-note" viewBox="0 0 24 24"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3z"/></symbol>'
    '<symbol id="g-star" viewBox="0 0 24 24"><path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.3 1.3-6.6-4.9-4.6 6.6-.8z"/></symbol>'
    '<symbol id="g-notes" viewBox="0 0 24 24"><path d="M21 3v12.5a3.5 3.5 0 1 1-2-3.16V7.6l-9 1.8v8.1a3.5 3.5 0 1 1-2-3.16V5.2z"/></symbol>'
    '<symbol id="g-diamond" viewBox="0 0 24 24"><path d="M12 2.5l8.5 9.5-8.5 9.5L3.5 12z"/></symbol>'
    '<symbol id="g-band" viewBox="0 0 24 24"><path d="M12 2a3.5 3.5 0 0 1 3.5 3.5v6a3.5 3.5 0 0 1-7 0v-6A3.5 3.5 0 0 1 12 2zM6 11h2a4 4 0 0 0 8 0h2a6 6 0 0 1-5 5.9V20h3v2H8v-2h3v-3.1A6 6 0 0 1 6 11z"/></symbol>'
    '<symbol id="g-clock" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zm0 2.6a7.4 7.4 0 1 0 0 14.8 7.4 7.4 0 0 0 0-14.8zM10.8 7h2.4v4.4l3.3 2-1.2 2-4.5-2.7z"/></symbol>'
    '<symbol id="g-spark" viewBox="0 0 24 24"><path d="M12 2Q13.4 10.6 22 12Q13.4 13.4 12 22Q10.6 13.4 2 12Q10.6 10.6 12 2z"/></symbol>'
    '<symbol id="g-smile" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zM7 9.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0zm7 0a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0zM7.2 13.5h9.6a4.8 4.8 0 0 1-9.6 0z"/></symbol>'
    '<symbol id="g-q" viewBox="0 0 24 24"><path d="M10.8 17.6h2.6v2.6h-2.6zM12.2 3.8c-2.7 0-4.6 1.7-4.8 4.3h2.6c.1-1.2 1-2 2.2-2 1.3 0 2.2.8 2.2 1.9 0 2.3-3.5 2.3-3.5 6v.6h2.6v-.5c0-2.6 3.5-2.8 3.5-6.2 0-2.4-2-4.1-4.8-4.1z"/></symbol>'
    + _sym("u-grid", '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>')
    + _sym("u-trophy", '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18M4 22h16M10 14.7V17c0 .6-.5 1-1 1.2C7.9 18.8 7 20.2 7 22M14 14.7V17c0 .6.5 1 1 1.2 1.1.6 2 2 2 3.8M18 2H6v7a6 6 0 0 0 12 0z"/>')
    + _sym("u-chart", '<path d="M3 3v18h18M8 17v-4M13 17V6M18 17v-8"/>')
    + _sym("u-music", '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>')
    + _sym("u-history", '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>')
    + _sym("u-clock", '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')
    + _sym("u-play", '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l5.5-3.5z"/>')
    + _sym("u-star", '<path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6l-5.4 2.9 1.2-6-4.5-4.2 6.1-.7z"/>')
    + _sym("u-check", '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7 5.8-5.7"/>')
    + _sym("u-flame", '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4.1 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>')
    + _sym("u-layers", '<path d="M12 2 2 7l10 5 10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>')
    + _sym("u-target", '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>')
    + _sym("u-spark", '<path d="M9.9 14.1 4 12l5.9-2.1L12 4l2.1 5.9L20 12l-5.9 2.1L12 20z"/>')
    + _sym("u-search", '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>')
    + _sym("u-right", '<path d="m9 6 6 6-6 6"/>')
    + _sym("u-left", '<path d="m15 6-6 6 6 6"/>')
    + _sym("u-first", '<path d="m17 6-6 6 6 6M7 6v12"/>')
    + _sym("u-last", '<path d="m7 6 6 6-6 6M17 6v12"/>')
    + _sym("u-medal", '<circle cx="12" cy="14" r="6"/><path d="M8.5 9.2 6 2h4l2 4 2-4h4l-2.5 7.2"/>')
    + _sym("u-lock", '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>')
    + _sym("u-archive", '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>')
    + _sym("u-download", '<path d="M12 3v12m0 0-4.5-4.5M12 15l4.5-4.5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>')
    + _sym("u-upload", '<path d="M12 15V3m0 0L7.5 7.5M12 3l4.5 4.5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>')
    + _sym("u-folder", '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>')
    + _sym("u-alert", '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>')
    + _sym("u-x", '<path d="M6 6l12 12M18 6 6 18"/>')
    + _sym("u-file", '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M10 12h4M10 16h4"/>')
    + "</svg>"
)


def _ic(name: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}" aria-hidden="true"><use href="#{name}"/></svg>'


# ---------------------------------------------------------------- 样式

CSS = """
:root{color-scheme:light dark;
--bg:#f3f4f8;--card:#fff;--sunk:#f6f7fb;--fg:#1b1f2e;--fg2:#3f4558;--muted:#6b7285;--faint:#9aa0b1;--line:#e5e7ef;--line2:#eef0f5;
--acc:#6a4cf0;--tint:rgba(124,92,255,.10);--grad:linear-gradient(135deg,#e4508f,#7c5cff);--good:#15803d;--goodbg:rgba(34,197,94,.12);
--ap:#7c5cff;--rest:#e4508f;--hl:rgba(124,92,255,.08);
--sh:0 1px 2px rgba(24,28,48,.04),0 4px 18px -6px rgba(24,28,48,.09);--r:14px}
@media (prefers-color-scheme:dark){:root{
--bg:#0f1116;--card:#181b22;--sunk:#13151b;--fg:#e9eaf0;--fg2:#c6cad6;--muted:#9399aa;--faint:#676d7e;--line:#272b35;--line2:#20242d;
--acc:#a58fff;--tint:rgba(150,125,255,.15);--good:#4ade80;--goodbg:rgba(74,222,128,.12);
--ap:#9479ff;--rest:#f06aa6;--hl:rgba(150,125,255,.10);
--sh:0 1px 0 rgba(255,255,255,.025) inset,0 6px 22px -10px rgba(0,0,0,.55)}}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{background:var(--bg)}
body{margin:0;color:var(--fg);font:14px/1.55 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit}
.sprite{position:absolute;width:0;height:0;overflow:hidden}
.ic{width:16px;height:16px;flex:none;fill:currentColor}
.wrap{max-width:1248px;margin:0 auto;padding:0 24px}
/* 顶栏 */
.top{position:sticky;top:0;z-index:30;background:color-mix(in srgb,var(--bg) 84%,transparent);backdrop-filter:blur(16px) saturate(1.5);border-bottom:1px solid var(--line)}
.topin{display:flex;align-items:center;gap:20px;min-height:64px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.logo{width:38px;height:38px;border-radius:11px;background:var(--grad);display:grid;place-items:center;color:#fff;flex:none;box-shadow:0 6px 14px -4px rgba(124,92,255,.55)}
.logo .ic{width:20px;height:20px}
.ttl{font-size:17px;font-weight:700;letter-spacing:.02em;line-height:1.25}
.meta{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tabs{display:flex;gap:2px;padding:4px;margin-left:auto;background:var(--sunk);border:1px solid var(--line);border-radius:12px}
.tabs button{display:flex;align-items:center;justify-content:center;gap:7px;border:0;background:none;color:var(--fg2);font-size:13.5px;padding:7px 14px;border-radius:9px;cursor:pointer;white-space:nowrap}
.tabs button:hover{background:var(--hl)}
.tabs .cnt{font-size:11.5px;color:var(--muted);background:var(--line2);border-radius:8px;padding:0 6px;line-height:18px}
.tabs .dot{width:7px;height:7px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 3px rgba(34,197,94,.22)}
.pane{display:none;padding:22px 0 48px}
/* 卡片 */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:18px 20px;min-width:0}
.h{display:flex;align-items:center;gap:8px;margin:0 0 14px;font-size:14.5px;font-weight:650;line-height:1.3}
.h>.ic{color:var(--acc);width:17px;height:17px}
.h .sub{font-weight:400;font-size:12.5px;color:var(--muted)}
.h .r{margin-left:auto;display:flex;align-items:center;gap:14px}
.lnk{display:inline-flex;align-items:center;gap:2px;border:0;background:none;padding:0;color:var(--acc);font-size:12.5px;cursor:pointer;white-space:nowrap}
.lnk:hover{text-decoration:underline}
.lnk .ic{width:14px;height:14px}
.muted{color:var(--muted)}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;margin-top:16px}
.mt{margin-top:16px}
/* 进度条 */
.bar{display:block;flex:1;height:6px;border-radius:3px;background:var(--line2);overflow:hidden;min-width:40px}
.bar i{display:block;height:100%;border-radius:3px;background:var(--grad)}
/* 概览：成就环 + 数字 */
.hero{display:grid;grid-template-columns:396px minmax(0,1fr);gap:16px}
.achcard{display:flex;gap:22px;align-items:center;position:relative;overflow:hidden}
.achcard::before{content:"";position:absolute;right:-90px;top:-110px;width:260px;height:260px;border-radius:50%;background:radial-gradient(closest-side,rgba(124,92,255,.10),transparent);pointer-events:none}
.ring{position:relative;width:156px;height:156px;flex:none}
.ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.ring .c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.ring b{font-size:40px;line-height:1;font-weight:700;letter-spacing:-.02em}
.ring b small{font-size:15px;color:var(--muted);font-weight:500;margin-left:2px;letter-spacing:0}
.ring .c span{font-size:12px;color:var(--muted);margin-top:6px}
.sg{fill:none;stroke-width:10}
.s0{stroke:var(--line)}.s1{stroke:#c98a4b}.s2{stroke:#a3aebd}.s3{stroke:#e8b52e}.s4{stroke:url(#lg)}
.ai{flex:1;min-width:0;position:relative}
.ak{font-size:13px;color:var(--muted);margin-bottom:8px}
.tiers{list-style:none;margin:0 0 10px;padding:0;display:flex;flex-direction:column;gap:7px}
.tiers li{display:flex;align-items:center;gap:8px;font-size:13px}
.tiers .tn{width:30px;color:var(--fg2)}
.tiers .bar{height:5px}
.tiers .bar i{background:var(--tcol)}
.tiers .tc{width:40px;text-align:right;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.tile{display:flex;flex-direction:column;padding:16px 18px 15px}
.tk{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--muted)}
.tk .ic{width:28px;height:28px;padding:6px;border-radius:8px;background:var(--tint);color:var(--acc)}
.tv{font-size:29px;font-weight:700;letter-spacing:-.01em;line-height:1.15;margin-top:10px;white-space:nowrap}
.tv small{font-size:14px;font-weight:500;color:var(--muted);margin-left:5px;letter-spacing:0}
.ts{font-size:12.5px;color:var(--muted);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .bar{flex:none;height:4px;margin-top:auto;transform:translateY(5px)}
.tile .ts+.bar{margin-top:10px}
.row3{display:grid;grid-template-columns:minmax(0,1.9fr) minmax(0,1fr) minmax(0,1fr);gap:16px;margin-top:16px}
/* 目标、最近解锁 */
.goal{display:flex;gap:12px;align-items:center;padding:10px 0;border-top:1px solid var(--line2);cursor:pointer}
.goal:first-of-type{border-top:0;padding-top:2px}
.gb{flex:1;min-width:0}
.gn{display:flex;align-items:center;gap:6px;font-weight:600;font-size:13.5px;white-space:nowrap}
.gn span:first-child{overflow:hidden;text-overflow:ellipsis}
.gd{font-size:12px;color:var(--muted);line-height:1.45}
.pb{display:flex;align-items:center;gap:8px;margin-top:6px;font-size:12px;color:var(--muted);white-space:nowrap}
.pct{margin-left:auto;padding-left:8px;font-weight:700;font-size:13px;color:var(--acc);font-variant-numeric:tabular-nums}
.when{font-size:12px;color:var(--muted);white-space:nowrap;text-align:right}
.new{font-size:10px;font-weight:700;letter-spacing:.06em;color:#fff;background:var(--grad);border-radius:5px;padding:0 5px;line-height:16px}
.goals3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 28px}
.goals3 .goal,.goals3 .goal:first-of-type{border-top:1px solid var(--line2);padding-top:10px}
/* 档位标签 */
.tier{flex:none;font-size:11px;font-weight:600;padding:0 7px;border-radius:999px;line-height:18px;color:var(--tc);background:var(--tb)}
.t1{--tl:#c98a4b;--tcol:#c98a4b;--tc:#8d5124;--tb:#f7e6d6;--m:linear-gradient(150deg,#ffe0bd,#d9935a 28%,#8f5228 52%,#c97f45 74%,#f7cfa3);--m2:radial-gradient(circle at 32% 28%,#f2c08f,#c47a3f 48%,#8f5229);--ink:#5a2f12;--emb:rgba(255,224,190,.7);--glow:rgba(201,138,75,.16);--gl2:rgba(150,90,40,.35);--to:#f3b57c}
.t2{--tl:#8f9aab;--tcol:#9ea8b7;--tc:#4b5565;--tb:#e8ebf0;--m:linear-gradient(150deg,#fff,#cfd5de 28%,#7f8999 52%,#b9c1cc 74%,#f5f7fa);--m2:radial-gradient(circle at 32% 28%,#fbfcfe,#c5ccd6 48%,#8d97a6);--ink:#38404d;--emb:rgba(255,255,255,.8);--glow:rgba(140,152,170,.16);--gl2:rgba(90,100,120,.30);--to:#dfe5ee}
.t3{--tl:#e3ae22;--tcol:#e3ae22;--tc:#8a6000;--tb:#fcefc4;--m:linear-gradient(150deg,#fff7cf,#f6c945 28%,#a5720a 52%,#e7b12b 74%,#fff1b3);--m2:radial-gradient(circle at 32% 28%,#ffec9a,#efbb2c 48%,#b07c0b);--ink:#5f3f00;--emb:rgba(255,246,205,.8);--glow:rgba(232,181,46,.20);--gl2:rgba(190,140,20,.40);--to:#ffd84f}
.t4{--tl:#a77bff;--tcol:linear-gradient(90deg,#ff6fb5,#a77bff,#4fc3ff);--tc:#fff;--tb:linear-gradient(90deg,#f45aa6,#9a6bff,#3fb5ff);--m:conic-gradient(from var(--ang),#ff6fb5,#ffb86b,#ffe66b,#7dffcf,#62c6ff,#a77bff,#ff6fb5);--m2:radial-gradient(circle at 32% 28%,#8a6bff,#4423b0 50%,#1d0f5c);--ink:#fff;--emb:rgba(255,120,190,.9);--glow:rgba(167,123,255,.22);--gl2:rgba(150,90,255,.55);--to:#ff9ad5}
@media (prefers-color-scheme:dark){.t1{--tc:#f2bd8c;--tb:rgba(201,138,75,.16)}.t2{--tc:#d3d9e2;--tb:rgba(170,180,195,.14)}.t3{--tc:#ffd560;--tb:rgba(232,181,46,.15)}}
/* 金属徽章 */
@property --ang{syntax:"<angle>";inherits:true;initial-value:0deg}
.badge{--s:46px;position:relative;flex:none;width:var(--s);height:var(--s);border-radius:50%;display:grid;place-items:center;background:var(--m);
box-shadow:inset 0 1px 1px rgba(255,255,255,.75),inset 0 -2px 3px rgba(0,0,0,.28),0 3px 8px -3px var(--gl2)}
.badge::before{content:"";position:absolute;inset:calc(var(--s)*.11);border-radius:50%;background:var(--m2);box-shadow:inset 0 2px 3px rgba(0,0,0,.30),0 1px 0 rgba(255,255,255,.55)}
.badge::after{content:"";position:absolute;inset:0;z-index:2;border-radius:50%;background:linear-gradient(120deg,transparent 38%,rgba(255,255,255,.55) 50%,transparent 62%) 50% 0/260% 100% no-repeat;opacity:.8;pointer-events:none}
.gi{position:relative;z-index:1;width:46%;height:46%;fill:var(--ink);filter:drop-shadow(0 1px 0 var(--emb))}
.badge.t4{box-shadow:inset 0 1px 1px rgba(255,255,255,.75),inset 0 -2px 3px rgba(0,0,0,.25),0 0 0 1px rgba(167,123,255,.25),0 2px 14px -2px rgba(214,98,255,.55);animation:spin 7s linear infinite}
.badge.t4::after{animation:sweep 4.5s ease-in-out infinite}
.badge.t4 .gi{filter:drop-shadow(0 0 3px rgba(255,140,210,.9))}
.badge.off{background:var(--sunk);box-shadow:inset 0 0 0 1px var(--line);animation:none}
.badge.off::before{inset:calc(var(--s)*.1);background:none;box-shadow:none;border:1.5px dashed color-mix(in srgb,var(--tl) 70%,transparent)}
.badge.off::after{display:none}
.badge.off .gi{fill:color-mix(in srgb,var(--tl) 40%,var(--faint));filter:none;opacity:.75}
.badge.xs.off::before{inset:2px;border-width:1px}
.badge.q{background:var(--sunk);box-shadow:inset 0 0 0 1.5px var(--line)}
.badge.q::before,.badge.q::after{display:none}
.badge.q .gi{fill:var(--faint);filter:none;width:44%;height:44%}
.badge.xs{--s:16px;box-shadow:inset 0 1px 1px rgba(255,255,255,.7),inset 0 -1px 2px rgba(0,0,0,.25)}
.badge.xs::before{inset:3px}
.badge.sm{--s:36px}.badge.lg{--s:62px}
@keyframes spin{to{--ang:360deg}}
@keyframes sweep{0%,55%{background-position:140% 0}100%{background-position:-40% 0}}
/* 徽章墙 */
.wg{display:grid;grid-template-columns:76px minmax(0,1fr);align-items:center;gap:14px;padding:9px 0}
.wg+.wg{border-top:1px solid var(--line2)}
.wgk{display:flex;flex-direction:column;gap:1px;font-size:12.5px;color:var(--fg2)}
.wgk b{font-size:11.5px;font-weight:500;color:var(--muted);font-variant-numeric:tabular-nums}
.wgb{display:flex;flex-wrap:wrap;gap:6px}
.wb{display:block;border-radius:50%;cursor:pointer;transition:transform .15s}
.wb:hover{transform:translateY(-2px) scale(1.08)}
/* 解锁弹出 */
.toasts{position:fixed;right:22px;bottom:22px;z-index:80;display:flex;flex-direction:column;gap:12px;align-items:flex-end}
.toast{position:relative;display:flex;align-items:center;gap:16px;width:360px;padding:16px 40px 19px 18px;border-radius:16px;color:#fff;cursor:pointer;overflow:hidden;
background:linear-gradient(135deg,#27213f,#15131f);box-shadow:0 18px 40px -12px rgba(10,6,30,.6),inset 0 0 0 1px color-mix(in srgb,var(--to) 32%,transparent);animation:tin .6s cubic-bezier(.2,1.1,.3,1) both}
.toast.t4{border:1.5px solid transparent;background:linear-gradient(135deg,#2c1754,#120b2a) padding-box,conic-gradient(from var(--ang),#ff6fb5,#ffe66b,#62c6ff,#a77bff,#ff6fb5) border-box;animation:tin .6s cubic-bezier(.2,1.1,.3,1) both,spin 4s linear infinite}
.to-glow{position:absolute;left:-60px;top:-80px;width:240px;height:240px;border-radius:50%;background:radial-gradient(closest-side,var(--gl2),transparent);opacity:.9;pointer-events:none}
.toast .badge{animation:pop .7s .15s cubic-bezier(.2,1.5,.4,1) both}
.toast .badge.t4{animation:pop .7s .15s cubic-bezier(.2,1.5,.4,1) both,spin 7s linear infinite}
.toast .badge::after{animation:sweep 1.8s .4s ease-out both}
.to-b{position:relative;min-width:0}
.to-k{font-size:12px;font-weight:600;letter-spacing:.12em;color:var(--to)}
.to-n{font-size:17px;font-weight:700;margin-top:1px}
.to-d{font-size:12.5px;color:rgba(255,255,255,.72)}
.to-x{position:absolute;right:8px;top:6px;border:0;background:none;color:rgba(255,255,255,.5);font-size:18px;line-height:1;padding:4px 6px;cursor:pointer}
.to-x:hover{color:#fff}
.to-l{position:absolute;left:0;right:0;bottom:0;height:3px;background:var(--to);transform-origin:left;animation:shrink 7s linear forwards}
.toast:hover .to-l{animation-play-state:paused}
.toast.out{animation:tout .4s ease forwards}
@keyframes tin{from{opacity:0;transform:translateX(40px) scale(.96)}}
@keyframes tout{to{opacity:0;transform:translateX(40px)}}
@keyframes pop{from{transform:scale(.3) rotate(-30deg);opacity:0}}
@keyframes shrink{to{transform:scaleX(0)}}
/* 进行中的运行 */
.live{display:flex;align-items:center;gap:8px 22px;flex-wrap:wrap;padding:12px 18px;margin-bottom:16px;border-radius:var(--r);border:1px solid rgba(34,197,94,.35);
background:linear-gradient(90deg,var(--goodbg),transparent 70%),var(--card);box-shadow:var(--sh)}
.live .lt{display:flex;align-items:center;gap:9px;font-weight:700}
.pulse{position:relative;width:9px;height:9px;border-radius:50%;background:#22c55e;flex:none}
.pulse::after{content:"";position:absolute;inset:-4px;border-radius:50%;border:2px solid #22c55e;opacity:0;animation:ping 1.8s ease-out infinite}
@keyframes ping{0%{transform:scale(.6);opacity:.8}100%{transform:scale(1.6);opacity:0}}
.live .li{font-size:12.5px;color:var(--muted)}
.live .li b{color:var(--fg);font-weight:650;margin-left:4px}
.live .lnk{margin-left:auto}
.live .lnow{flex:1 0 100%;display:flex;align-items:center;gap:8px;margin-top:2px;padding-top:9px;border-top:1px dashed var(--line);font-size:12.5px;color:var(--muted);min-width:0}
.live .lnow .ls{width:15px;height:15px;color:var(--good);flex:none}
.live .lnow .lk{color:var(--fg);font-weight:650;flex:none}
.live .lnow .ld{color:var(--fg2);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.live .lnow .lx{margin-left:auto;flex:none;font-variant-numeric:tabular-nums}
.live.stale{border-color:rgba(217,119,6,.45);background:linear-gradient(90deg,rgba(217,119,6,.10),transparent 70%),var(--card)}
.live.stale .pulse{background:var(--faint);animation:none}
.live.stale .pulse::after{display:none}
.live.stale .lnow .ls{color:rgba(217,119,6,.9)}
/* 空 */
.empty{display:flex;align-items:center;gap:28px;padding:26px 30px}
.empty h2{font-size:21px;margin:0 0 6px;letter-spacing:.01em}
.empty p{margin:0;color:var(--muted);max-width:560px}
.empty ul{display:flex;flex-wrap:wrap;gap:8px;list-style:none;margin:14px 0 0;padding:0}
.empty li{font-size:12.5px;color:var(--fg2);background:var(--sunk);border:1px solid var(--line);border-radius:999px;padding:3px 11px}
.art{width:120px;height:120px;flex:none}
.none{padding:40px 0;text-align:center;color:var(--muted)}
/* 柱状图 */
.chart{position:relative;padding-left:38px}
.card.fx{display:flex;flex-direction:column}
.chart.grow{flex:1;display:flex;flex-direction:column}
.chart.grow .plot{flex:1;height:auto;min-height:var(--h)}
.chart.grow .yax{height:auto;bottom:23px}
.plot{position:relative;height:var(--h)}
.plot svg{position:absolute;inset:0;display:block;width:100%;height:100%;overflow:visible}
.grid line{stroke:var(--line2);stroke-width:1;vector-effect:non-scaling-stroke}
.grid line.base{stroke:var(--line)}
.c-ap{fill:var(--ap)}.c-rest{fill:var(--rest)}
.col .hit{fill:transparent}
.col:hover .hit{fill:var(--hl)}
.col.now .c-ap{fill:url(#bg)}
.yax{position:absolute;left:0;top:0;width:30px;height:var(--h)}
.yax span,.xax span{position:absolute;font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums;white-space:nowrap;line-height:1}
.yax span{right:0;transform:translateY(50%)}
.xax{position:relative;height:16px;margin-top:7px}
.xax span{transform:translateX(-50%)}
.legend{display:flex;align-items:center;gap:14px;font-size:12px;color:var(--muted);font-weight:400}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.blank{position:absolute;inset:0;display:grid;place-items:center;color:var(--faint);font-size:13px}
.tip{position:fixed;z-index:90;pointer-events:none;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:10px;padding:7px 11px;box-shadow:0 12px 30px -8px rgba(0,0,0,.28);font-size:12px;max-width:300px}
.tip .th{color:var(--muted)}.tip .tv2{font-weight:650;font-size:13px}
/* 成就页 */
.achbar{display:flex;gap:26px;align-items:center}
.achbar .ring{width:104px;height:104px}
.achbar .ring b{font-size:26px}
.achbar .ring b small{font-size:13px}
.filters{display:flex;flex-direction:column;gap:10px;flex:1;min-width:0}
.frow{display:flex;align-items:center;gap:10px}
.flab{flex:none;width:30px;font-size:12px;color:var(--muted)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--card);color:var(--fg2);font-size:13px;padding:3px 12px;border-radius:999px;cursor:pointer;line-height:20px}
.chip:hover{border-color:var(--acc);color:var(--fg)}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
.chip i{font-style:normal;font-size:11.5px;opacity:.7;font-variant-numeric:tabular-nums}
.chip .ic{width:14px;height:14px}
.chip .badge.xs{--s:14px}
.agroup{margin-top:24px}
.agh{display:flex;align-items:center;gap:10px;margin:0 2px 12px;font-size:15px;font-weight:650}
.agh>.ic{width:18px;height:18px;color:var(--acc)}
.agh .n{font-size:12.5px;font-weight:500;color:var(--muted);font-variant-numeric:tabular-nums}
.agh .bar{flex:none;width:110px;height:5px}
.agrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(272px,1fr));gap:12px}
.ach{display:flex;gap:14px;align-items:center;padding:14px 16px;border-radius:12px;background:var(--card);border:1px solid var(--line);box-shadow:var(--sh);position:relative;overflow:hidden;min-height:92px}
.ach.done::before{content:"";position:absolute;inset:0;background:radial-gradient(160px 110px at 0 0,var(--glow),transparent 75%);pointer-events:none}
.ach.done.t4{border-color:rgba(167,123,255,.45)}
.ach .badge{--s:54px}
.ab{flex:1;min-width:0;position:relative}
.an{display:flex;align-items:center;gap:7px;font-weight:650;font-size:14px}
.an .tier{margin-left:auto}
.ad{font-size:12.5px;color:var(--muted);margin-top:1px}
.af{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--good);margin-top:6px}
.af .ic{width:14px;height:14px}
.ach .pb{margin-top:7px}
.ach.todo .an{color:var(--fg2)}
.ach.secret{border-style:dashed;box-shadow:none;background:transparent}
.ach.secret .an{color:var(--muted);letter-spacing:.2em}
.ach.flash{animation:flash 1.8s ease}
@keyframes flash{0%,40%{box-shadow:0 0 0 3px var(--acc),var(--sh)}}
.hid{flex:none;font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:0 6px;line-height:16px;font-weight:500}
/* 数字条 */
.strip{display:grid;grid-template-columns:repeat(var(--n,6),minmax(0,1fr));padding:16px 4px}
.fig{padding:2px 18px;border-left:1px solid var(--line2);min-width:0}
.fig:first-child{border-left:0}
.fk{font-size:12.5px;color:var(--muted);white-space:nowrap}
.fv{font-size:21px;font-weight:700;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;letter-spacing:-.01em}
.fv small{font-size:12.5px;font-weight:500;color:var(--muted);margin-left:3px;letter-spacing:0}
.fs{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* 难度 */
.dg{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;padding:0 7px;border-radius:6px;line-height:19px;color:var(--dc);background:color-mix(in srgb,var(--dc) 13%,transparent)}
.d0{--dc:#2563eb}.d1{--dc:#15803d}.d2{--dc:#b45309}.d3{--dc:#dc2626}.dx{--dc:#6b7285}
@media (prefers-color-scheme:dark){.d0{--dc:#6ba6ff}.d1{--dc:#4ade80}.d2{--dc:#fbbf24}.d3{--dc:#ff7a7a}.dx{--dc:#9399aa}}
.drows{display:flex;flex-direction:column}
.drow{display:grid;grid-template-columns:74px minmax(0,1fr) 92px 112px;align-items:center;gap:14px;padding:11px 0;border-top:1px solid var(--line2);font-size:13px}
.drow:first-child{border-top:0;padding-top:0}
.drow.hd{font-size:12px;color:var(--muted);padding:0 0 8px;border-top:0}
.drow .bar{height:8px;border-radius:4px}
.drow .bar i{border-radius:4px;background:var(--ap)}
.drow .v{text-align:right;white-space:nowrap}
.drow .v small{color:var(--muted);font-size:12px;margin-left:4px}
.top10{display:flex;flex-direction:column}
.tr{display:grid;grid-template-columns:20px minmax(0,1fr) auto 90px 58px;align-items:center;gap:12px;padding:8px 0;border-top:1px solid var(--line2);font-size:13px}
.tr:first-child{border-top:0;padding-top:0}
.tr .rk{color:var(--faint);font-size:12px;font-variant-numeric:tabular-nums;text-align:right}
.tr .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tr .bar{height:6px}
.tr .bar i{background:var(--ap)}
.tr .v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.facts{display:grid;grid-template-columns:auto 1fr;margin:0}
.facts dt,.facts dd{padding:8px 0;border-top:1px solid var(--line2);margin:0;font-size:13px}
.facts dt{color:var(--muted);padding-right:20px;white-space:nowrap}
.facts dd{text-align:right}
.facts dt:first-of-type,.facts dd:first-of-type{border-top:0;padding-top:0}
/* 表格 */
.tbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 16px;border-bottom:1px solid var(--line)}
.search{position:relative;flex:1;min-width:200px;max-width:340px}
.search .ic{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--faint)}
.search input{width:100%;font:inherit;font-size:13.5px;color:var(--fg);background:var(--sunk);border:1px solid var(--line);border-radius:9px;padding:6px 10px 6px 34px;outline:none}
.search input:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--tint);background:var(--card)}
.seg{display:flex;padding:3px;gap:2px;background:var(--sunk);border:1px solid var(--line);border-radius:9px}
.seg button{border:0;background:none;font-size:12.5px;color:var(--fg2);padding:3px 10px;border-radius:6px;cursor:pointer;white-space:nowrap}
.seg button:hover{background:var(--hl)}
.seg button.on{background:var(--card);color:var(--fg);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08),0 0 0 1px var(--line)}
.seg i{font-style:normal;color:var(--muted);font-size:11.5px;margin-left:4px;font-weight:400}
.switch{display:inline-flex;align-items:center;gap:9px;border:0;background:none;font-size:13px;color:var(--fg2);cursor:pointer;padding:2px 0;white-space:nowrap}
.switch .k{position:relative;width:34px;height:20px;border-radius:10px;background:var(--line);transition:background .15s;flex:none}
.switch .k::after{content:"";position:absolute;left:2px;top:2px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);transition:transform .15s}
.switch[aria-checked=true] .k{background:var(--acc)}
.switch[aria-checked=true] .k::after{transform:translateX(14px)}
.switch b{font-weight:600;color:var(--fg)}
.count{margin-left:auto;font-size:12.5px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.tblcard{padding:0;overflow:hidden}
.tw{max-height:calc(100vh - 196px);min-height:260px;overflow:auto}
table{width:100%;border-collapse:separate;border-spacing:0;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;z-index:1;background:var(--card);font-size:12px;font-weight:600;color:var(--muted);text-align:left;padding:9px 14px;border-bottom:1px solid var(--line);white-space:nowrap;user-select:none}
td{padding:8px 14px;border-bottom:1px solid var(--line2);white-space:nowrap;font-size:13px}
tbody tr:hover td{background:var(--sunk)}
tbody tr:last-child td{border-bottom:0}
.num{text-align:right}
td.t{white-space:normal;min-width:180px;max-width:380px}
#charts th{cursor:pointer}
#charts th:hover{color:var(--fg)}
#charts th[data-dir]::after{content:"";display:inline-block;margin-left:6px;border:4px solid transparent;vertical-align:1px}
#charts th[data-dir=up]::after{border-bottom-color:currentColor;vertical-align:3px}
#charts th[data-dir=down]::after{border-top-color:currentColor;vertical-align:-1px}
#charts th[data-dir=up],#charts th[data-dir=down]{color:var(--acc)}
.rate{display:inline-block;width:52px;height:5px;border-radius:3px;background:var(--line2);vertical-align:middle;margin-right:9px;overflow:hidden}
.rate i{display:block;height:100%;border-radius:3px;background:var(--ap)}
.jd{color:var(--muted);font-size:12.5px}
.vd{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.04em;padding:0 6px;border-radius:5px;line-height:17px}
.vd.ap{color:#fff;background:var(--grad)}
.vd.fc{color:var(--d);--d:#2563eb;background:color-mix(in srgb,var(--d) 13%,transparent)}
.vd.no{color:var(--muted);background:var(--line2);font-weight:500}
@media (prefers-color-scheme:dark){.vd.fc{--d:#6ba6ff}}
.jd .vd{margin-left:8px}
.ltag{display:inline-flex;align-items:center;gap:6px;margin-left:8px;font-size:11.5px;font-weight:600;color:var(--good);background:var(--goodbg);border-radius:999px;padding:0 9px;line-height:20px}
.ltag .pulse{width:7px;height:7px}
.ltag .pulse::after{inset:-3px;border-width:1.5px}
.dur{display:flex;align-items:center;gap:10px}
.dur .bar{width:90px;flex:none;height:5px}
.dur .bar i{background:var(--ap)}
tr.on td{background:color-mix(in srgb,var(--goodbg) 60%,transparent)}
.note{display:flex;gap:12px;align-items:flex-start;font-size:13px;color:var(--fg2);line-height:1.6}
.note>.ic{color:var(--acc);margin-top:3px}
/* 谱面：一首歌一组，点开看每个难度 */
#charts tbody tr:last-child td{border-bottom:1px solid var(--line2)}
#charts tr.sr{cursor:pointer}
#charts tr.sr:focus-visible{outline:2px solid var(--acc);outline-offset:-2px}
#charts tr.sd{display:none}
#charts tbody.open tr.sd{display:table-row}
#charts tbody.open tr.sr td{background:var(--sunk);border-bottom-color:transparent}
#charts tr.sd td,#charts tr.sd:hover td{background:var(--sunk);padding:2px 14px 16px 40px;white-space:normal}
.sn{display:flex;align-items:center;gap:8px;min-width:0}
.sn .chev{width:14px;height:14px;color:var(--faint);transition:transform .15s}
tbody.open .sn .chev{transform:rotate(90deg);color:var(--acc)}
.sn .nm{display:block;font-weight:600}
.sn small{display:block;font-size:11.5px;color:var(--muted);line-height:16px}
td.dc{padding:6px 10px;transition:opacity .15s}
.dcl{display:flex;align-items:center;gap:8px}
.lv{flex:none;min-width:30px;height:26px;padding:0 6px;box-sizing:border-box;border-radius:7px;display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:var(--dc);background:color-mix(in srgb,var(--dc) 13%,transparent)}
.lv.ap{color:#fff;background:var(--dc)}
.lv.none{background:none;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--dc) 35%,transparent);color:color-mix(in srgb,var(--dc) 50%,var(--faint))}
.ds{display:flex;flex-direction:column;font-size:12px;line-height:17px;min-width:0}
.ds small{font-size:11px;color:var(--muted);line-height:14px}
.gap{color:var(--fg2);font-weight:600;font-size:12px}
#charts[data-cd="0"] .dc:not(.c0),#charts[data-cd="1"] .dc:not(.c1),#charts[data-cd="2"] .dc:not(.c2),#charts[data-cd="3"] .dc:not(.c3),#charts[data-na="1"] .dc:not(.todo){opacity:.35}
.dcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.dcard{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:12.5px;display:flex;flex-direction:column;gap:6px}
.dcard.none{border-style:dashed;background:transparent}
.dch{display:flex;align-items:center;gap:8px}
.dch .vd{margin-left:auto}
.lvt{font-size:12px;font-weight:600;color:var(--fg2)}
.dcn{color:var(--fg2)}
.dcr{display:flex;align-items:center;font-size:12px;color:var(--muted)}
.dcard dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;margin:0}
.dcard dt{color:var(--muted)}
.dcard dd{margin:0;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* 翻页 */
.pgc{scroll-margin-top:84px}
.pager{display:flex;align-items:center;justify-content:center;gap:6px;padding:10px 16px;border-top:1px solid var(--line)}
.pager button{display:inline-flex;align-items:center;gap:4px;font:inherit;font-size:12.5px;color:var(--fg2);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 10px;cursor:pointer}
.pager button:hover:not(:disabled){background:var(--hl);color:var(--fg)}
.pager button:disabled{opacity:.4;cursor:default}
.pager .ic{width:14px;height:14px}
.pager .pi{min-width:190px;text-align:center;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
/* 备份与恢复 */
.tool{display:inline-flex;align-items:center;gap:7px;flex:none;height:44px;padding:0 15px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg2);font-size:13.5px;cursor:pointer;white-space:nowrap;box-shadow:var(--sh);transition:border-color .15s,color .15s}
.tool .ic{color:var(--acc)}
.tool:hover{color:var(--fg);border-color:color-mix(in srgb,var(--acc) 45%,var(--line))}
.tool:focus-visible,.btn:focus-visible,.bk-x:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.note .lnk{margin-left:8px;vertical-align:-1px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;font-size:13.5px;font-weight:600;padding:8px 16px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;white-space:nowrap;transition:background .15s,border-color .15s,box-shadow .15s,filter .15s}
.btn:hover:not(:disabled){border-color:color-mix(in srgb,var(--acc) 45%,var(--line));background:var(--hl)}
.btn.pri{border-color:transparent;color:#fff;background:var(--grad);box-shadow:0 6px 16px -6px rgba(124,92,255,.6)}
.btn.pri:hover:not(:disabled){background:var(--grad);border-color:transparent;box-shadow:0 8px 20px -6px rgba(124,92,255,.75);filter:brightness(1.06)}
.btn:disabled{opacity:.45;cursor:default;box-shadow:none}
.btn .ic{width:16px;height:16px}
dialog.bk{width:min(700px,calc(100vw - 32px));max-width:none;max-height:calc(100vh - 40px);padding:0;border:1px solid var(--line);border-radius:18px;background:var(--card);color:var(--fg);box-shadow:0 30px 80px -20px rgba(10,8,30,.45);overflow:hidden}
dialog.bk::backdrop{background:rgba(16,14,30,.42);backdrop-filter:blur(3px)}
dialog.bk[open]{animation:din .22s cubic-bezier(.2,1,.3,1)}
@keyframes din{from{opacity:0;transform:translateY(10px) scale(.98)}}
.bk-in{padding:22px 24px 20px;overflow:auto;max-height:calc(100vh - 42px)}
.bk-hd{display:flex;align-items:flex-start;gap:14px;margin-bottom:18px}
.bk-hd .logo{width:42px;height:42px}
.bk-hd h2{margin:0;font-size:18px;line-height:1.3}
.bk-hd p{margin:3px 0 0;font-size:13px;color:var(--muted)}
.bk-x{margin-left:auto;flex:none;display:grid;place-items:center;width:32px;height:32px;border:0;border-radius:9px;background:none;color:var(--muted);cursor:pointer}
.bk-x:hover{background:var(--hl);color:var(--fg)}
.bk-off{display:flex;gap:10px;align-items:flex-start;padding:11px 14px;margin-bottom:16px;border-radius:12px;font-size:13px;line-height:1.6;color:var(--fg2);background:rgba(217,119,6,.10);border:1px solid rgba(217,119,6,.32)}
.bk-off .ic{color:#d97706;margin-top:3px}
.bk-cards{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}
.bk-card{display:flex;flex-direction:column;gap:10px;padding:16px 18px;border-radius:14px;border:1px solid var(--line);background:var(--sunk)}
.bk-ct{display:flex;align-items:center;gap:10px;font-weight:650;font-size:15px}
.bk-ct .ic{width:30px;height:30px;padding:7px;border-radius:9px;background:var(--tint);color:var(--acc)}
.bk-card p{margin:0;font-size:12.5px;color:var(--muted);line-height:1.6;flex:1}
.bk-sum{display:flex;flex-wrap:wrap;gap:6px}
.bk-sum span{font-size:12px;color:var(--fg2);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:1px 10px;font-variant-numeric:tabular-nums}
.bk-dir{margin-top:14px;font-size:12.5px}
.bk-dir summary{cursor:pointer;color:var(--acc);list-style:none;display:inline-flex;align-items:center;gap:3px}
.bk-dir summary::-webkit-details-marker{display:none}
.bk-dir summary:hover{text-decoration:underline}
.bk-dir summary .ic{width:14px;height:14px;transition:transform .15s}
.bk-dir[open] summary .ic{transform:rotate(90deg)}
.bk-row{display:flex;gap:8px;margin-top:8px}
.bk-row input{flex:1;min-width:0;font:inherit;font-size:13px;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:9px;padding:6px 10px;outline:none}
.bk-row input:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--tint)}
.bk-row input:disabled{opacity:.5}
.bk-row .btn{padding:6px 12px;font-size:12.5px}
.bk-dir small{display:block;margin-top:6px;color:var(--muted);font-size:11.5px;line-height:1.5}
.bk-pv{margin-top:16px;padding:16px 18px;border-radius:14px;border:1px solid color-mix(in srgb,var(--acc) 35%,var(--line));background:linear-gradient(135deg,var(--tint),transparent 60%),var(--card);animation:din .2s ease}
.bk-pvh{display:flex;align-items:center;gap:10px;min-width:0}
.bk-pvh .ic{width:20px;height:20px;color:var(--acc)}
.bk-pvh b{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.bk-pvs{margin:2px 0 0 30px;font-size:12px;color:var(--muted)}
.bk-st{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0 0;padding:0;list-style:none}
.bk-st li{padding:10px 12px;border-radius:11px;background:var(--card);border:1px solid var(--line);min-width:0}
.bk-st b{display:block;font-size:20px;line-height:1.25;font-variant-numeric:tabular-nums;background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.bk-st li.z b{background:none;color:var(--faint)}
.bk-st span{font-size:12px;color:var(--muted)}
.bk-st small{display:block;font-size:11px;color:var(--faint);margin-top:2px;line-height:1.4}
.bk-note{margin:12px 0 0;font-size:12.5px;color:var(--fg2)}
.bk-act{display:flex;justify-content:flex-end;gap:10px;margin-top:16px}
.bk-msg{display:flex;align-items:flex-start;gap:9px;margin-top:14px;padding:10px 14px;border-radius:11px;font-size:13px;line-height:1.55;background:var(--sunk);border:1px solid var(--line);color:var(--fg2);overflow-wrap:anywhere}
.bk-msg .ic,.bk-msg .spin{margin-top:2px}
.bk-msg.ok{background:var(--goodbg);border-color:color-mix(in srgb,var(--good) 35%,transparent)}
.bk-msg.ok .ic{color:var(--good)}
.bk-msg.err{background:rgba(220,38,38,.08);border-color:rgba(220,38,38,.3)}
.bk-msg.err .ic{color:#dc2626}
.bk-foot{margin:16px 0 0;font-size:11.5px;color:var(--faint);line-height:1.6}
.spin{width:16px;height:16px;flex:none;border-radius:50%;border:2px solid color-mix(in srgb,var(--acc) 25%,transparent);border-top-color:var(--acc);animation:rot .8s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
.notice{position:fixed;left:50%;bottom:24px;z-index:85;display:flex;align-items:flex-start;gap:10px;width:max-content;max-width:min(600px,calc(100vw - 32px));padding:12px 12px 12px 16px;border-radius:14px;background:var(--card);border:1px solid color-mix(in srgb,var(--good) 40%,var(--line));box-shadow:0 18px 40px -14px rgba(10,8,30,.4);font-size:13px;line-height:1.55;transform:translateX(-50%);animation:nin .35s cubic-bezier(.2,1.1,.3,1);overflow-wrap:anywhere}
.notice>.ic{color:var(--good);margin-top:2px}
.notice button{flex:none;border:0;background:none;color:var(--muted);cursor:pointer;padding:0 4px;font-size:17px;line-height:1}
.notice button:hover{color:var(--fg)}
@keyframes nin{from{opacity:0;transform:translate(-50%,12px)}}
/* 窄窗口 */
@media (max-width:1180px){
.hero{grid-template-columns:300px minmax(0,1fr)}
.achcard{flex-direction:column;align-items:stretch;gap:14px}
.achcard .ring{align-self:center}
.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
.row3{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.row3>:first-child{grid-column:1/-1}
.strip{grid-template-columns:repeat(3,minmax(0,1fr));row-gap:14px}
.fig:nth-child(3n+1){border-left:0}
.goals3{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:1040px){
.topin{flex-wrap:wrap;padding-top:10px;padding-bottom:12px;row-gap:10px}
.tabs{margin-left:0;width:100%}
.tabs button{flex:1}
.tool{order:2;margin-left:auto}
.tabs{order:3}
.tw{max-height:calc(100vh - 252px)}
.achbar{flex-direction:column;align-items:stretch;gap:16px}
.achbar .ring{display:none}
}
@media (max-width:760px){
.hero,.grid2,.row3{grid-template-columns:minmax(0,1fr)}
.goals3{grid-template-columns:minmax(0,1fr)}
.bk-cards{grid-template-columns:minmax(0,1fr)}
.bk-st{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
"""

# 页签、成就筛选、谱面表（搜索、难度、只看没 AP、排序、展开）、运行记录翻页、悬停提示；界面状态存 sessionStorage，自动重新载入后恢复
# （LIVE_JS 在 load 时才恢复滚动位置，这里先同步恢复状态）。
PAGE_JS = """(()=>{
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)],root=document.documentElement,KEY='stats-ui';
let st={};
try{st=JSON.parse(sessionStorage.getItem(KEY))||{}}catch(e){}
if(!st||typeof st!=='object'||Array.isArray(st))st={};
const save=()=>{try{sessionStorage.setItem(KEY,JSON.stringify(st))}catch(e){}};
const panes=$$('[data-pane]').map(p=>p.dataset.pane);
function tab(t){if(!panes.includes(t))t='ov';root.dataset.tab=t;st.tab=t;$$('.tabs [data-t]').forEach(b=>b.setAttribute('aria-selected',b.dataset.t===t));save();}
tab(root.dataset.tab);
$$('.tabs [data-t]').forEach(b=>b.onclick=()=>{tab(b.dataset.t);scrollTo(0,0);});
const AF={ast:'all',ag:'all',at:'all'};
function achApply(){
$$('[data-af]').forEach(box=>{const k=box.dataset.af,vs=$$('[data-v]',box).map(b=>b.dataset.v);if(!vs.includes(st[k]))st[k]=AF[k];$$('[data-v]',box).forEach(b=>b.classList.toggle('on',b.dataset.v===st[k]));});
for(const k in AF)if(typeof st[k]!=='string')st[k]=AF[k];
let n=0;
$$('.agroup').forEach(g=>{let m=0;$$('.ach',g).forEach(c=>{const ok=(st.ast==='all'||c.dataset.st===st.ast)&&(st.ag==='all'||g.dataset.g===st.ag)&&(st.at==='all'||c.dataset.tier===st.at);c.hidden=!ok;m+=ok;});g.hidden=!m;n+=m;});
const none=$('#ach-none');if(none)none.hidden=n>0;
}
$$('[data-af]').forEach(box=>box.addEventListener('click',e=>{const b=e.target.closest('[data-v]');if(!b)return;st[box.dataset.af]=b.dataset.v;achApply();save();}));
achApply();
let filt=()=>{};
const tbl=$('#charts');
if(tbl){
const bodies=$$('tbody',tbl),ths=$$('th',tbl),q=$('#q'),na=$('#notap'),cnt=$('#cnt'),tw=$('.tw'),none=$('#charts-none'),cf=$('[data-cf]'),cds=$$('[data-v]',cf).map(b=>b.dataset.v);
const C={q:'',cd:'all',na:false};
for(const k in C)if(typeof st[k]!==typeof C[k])st[k]=C[k];
if(!Array.isArray(st.op))st.op=[];
filt=()=>{if(!cds.includes(st.cd))st.cd='all';const s=st.q.trim().toLowerCase();let n=0;
bodies.forEach(b=>{const d=b.dataset,h=(!!s&&!d.k.includes(s))||(st.cd==='all'?st.na&&!d.n:!(st.na?d.n:d.p).includes(st.cd));b.hidden=h;n+=!h;});
cnt.textContent='显示 '+n.toLocaleString()+' / '+bodies.length.toLocaleString()+' 首';
none.hidden=n>0;na.setAttribute('aria-checked',String(st.na));tbl.dataset.cd=st.cd;tbl.dataset.na=st.na?'1':'';
$$('[data-v]',cf).forEach(b=>b.classList.toggle('on',b.dataset.v===st.cd));};
const open=(b,on)=>{b.classList.toggle('open',on);b.rows[0].setAttribute('aria-expanded',String(on));};
bodies.forEach(b=>{const m=b.dataset.m,r=b.rows[0];if(st.op.includes(m))open(b,true);
const flip=()=>{const on=!b.classList.contains('open');open(b,on);st.op=st.op.filter(x=>x!==m);if(on)st.op.push(m);save();};
r.onclick=flip;r.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();flip();}};});
const key=(b,i,kind)=>{const c=b.rows[0].cells[i];if(!c)return 0;const v=c.dataset.v;if(kind==='t')return v===undefined?c.textContent:v;return v===undefined?parseFloat(c.textContent.replace(/[^0-9.-]/g,''))||0:+v;};
function sort(){const i=st.sc;ths.forEach((th,j)=>{if(j===i)th.dataset.dir=st.sd==='up'?'up':'down';else delete th.dataset.dir;});
if(typeof i!=='number'||!ths[i])return;const kind=ths[i].dataset.s,up=st.sd==='up',ks=new Map(bodies.map(b=>[b,key(b,i,kind)]));
const arr=bodies.slice().sort((a,b)=>{const x=ks.get(a),y=ks.get(b),d=kind==='t'?String(x).localeCompare(String(y),'zh'):x-y;return up?d:-d;});
const f=document.createDocumentFragment();arr.forEach(b=>f.appendChild(b));tbl.appendChild(f);}
q.value=st.q;
q.addEventListener('input',()=>{st.q=q.value;filt();save();});
q.addEventListener('keydown',e=>{if(e.key==='Escape'){q.value='';st.q='';filt();save();}});
na.onclick=()=>{st.na=!st.na;filt();save();};
cf.addEventListener('click',e=>{const b=e.target.closest('[data-v]');if(!b)return;st.cd=b.dataset.v;filt();save();});
ths.forEach((th,i)=>th.onclick=()=>{st.sd=st.sc===i?(st.sd==='up'?'down':'up'):(th.dataset.s==='t'?'up':'down');st.sc=i;sort();save();});
let tmr=0;tw.addEventListener('scroll',()=>{clearTimeout(tmr);tmr=setTimeout(()=>{st.ts=tw.scrollTop;save();},150);},{passive:true});
filt();sort();
const ts=+st.ts||0;if(ts){tw.scrollTop=ts;addEventListener('load',()=>{tw.scrollTop=ts;});}
}
function pager(k,show){const box=$('[data-pager="'+k+'"]');if(!box)return;const ps=+box.dataset.ps||50,n=+box.dataset.n||0,pages=Math.max(1,Math.ceil(n/ps)),sk='pg'+k,pi=$('.pi',box);
const go=p=>{p=Math.min(pages-1,Math.max(0,Math.floor(+p)||0));st[sk]=p;show(p*ps,Math.min(n,p*ps+ps));
pi.textContent='第 '+(p+1)+' / '+pages+' 页 · 共 '+n.toLocaleString()+' '+box.dataset.u;
$$('[data-p]',box).forEach(b=>{b.disabled=b.dataset.p==='first'||b.dataset.p==='prev'?p===0:p===pages-1;});};
box.addEventListener('click',e=>{const b=e.target.closest('[data-p]');if(!b||b.disabled)return;const d=b.dataset.p,p=st[sk];
go(d==='first'?0:d==='prev'?p-1:d==='next'?p+1:pages-1);save();const c=box.closest('.card');if(c&&c.getBoundingClientRect().top<0)c.scrollIntoView({block:'start'});});
go(st[sk]);}
const sp=$('[data-pg="sp"]');
if(sp){const rows=[...sp.rows];pager('sp',(a,b)=>rows.forEach((r,i)=>{r.hidden=i<a||i>=b;}));}
const pd=$('#plays-data'),pt=$('#plays tbody');
if(pd&&pt){let D=null;try{D=JSON.parse(pd.textContent)}catch(e){}
if(D&&Array.isArray(D.r)){const esc=s=>String(s).replace(/[&<>"]/g,c=>'&#'+c.charCodeAt(0)+';'),u=x=>x==null?'?':x,p2=x=>String(x).padStart(2,'0'),Y=new Date().getFullYear(),
V=['','<span class="vd fc">FC</span>','<span class="vd ap">AP</span>','<span class="vd no">结果没读全</span>'];
const when=ts=>{const d=new Date(ts*1000);return (d.getFullYear()===Y?'':d.getFullYear()+'-')+p2(d.getMonth()+1)+'-'+p2(d.getDate())+' '+p2(d.getHours())+':'+p2(d.getMinutes());};
const row=([ts,m,d,p,g,gd,b,ms,v,sc])=>{const k=typeof d==='number';
return '<tr><td>'+when(ts)+'</td><td class="t">'+esc(D.t[m]??m)+'</td><td><span class="dg '+(k?'d'+d:'dx')+'">'+esc(String(k?D.d[d]:d).toUpperCase())+'</span></td>'
+'<td class="jd">P'+u(p)+' G'+u(g)+' g'+u(gd)+' B'+u(b)+' M'+u(ms)+'</td><td>'+(V[v]||'')+'</td><td class="num">'+(sc==null?'-':sc.toLocaleString())+'</td></tr>';};
pager('pp',(a,b)=>{pt.innerHTML=D.r.slice(a,b).map(row).join('');});}}
window.statsGoto=id=>{st.ast='all';st.ag='all';st.at='all';achApply();tab('ach');const c=document.getElementById('a-'+id);
if(c){c.scrollIntoView({block:'center'});c.classList.remove('flash');void c.offsetWidth;c.classList.add('flash');}else scrollTo(0,0);};
document.addEventListener('click',e=>{const a=e.target.closest('[data-a]');if(a){statsGoto(a.dataset.a);return;}
const g=e.target.closest('[data-go]');if(!g)return;if(g.dataset.na){st.na=true;st.q='';st.cd='all';const q=$('#q');if(q)q.value='';filt();}
tab(g.dataset.go);scrollTo(0,0);});
const tip=document.createElement('div');tip.className='tip';tip.hidden=true;document.body.appendChild(tip);
let cur=null;
function place(e){const w=tip.offsetWidth,h=tip.offsetHeight;let x=e.clientX+14,y=e.clientY+16;if(x+w>innerWidth-8)x=e.clientX-w-12;if(y+h>innerHeight-8)y=e.clientY-h-12;tip.style.left=Math.max(4,x)+'px';tip.style.top=Math.max(4,y)+'px';}
document.addEventListener('pointermove',e=>{const t=e.target.closest?e.target.closest('[data-tip]'):null;
if(t!==cur){cur=t;if(!t){tip.hidden=true;return;}const [h,...v]=t.dataset.tip.split('\\n');tip.replaceChildren();
const a=document.createElement('div');a.className='th';a.textContent=h;tip.appendChild(a);
v.forEach(s=>{const b=document.createElement('div');b.className='tv2';b.textContent=s;tip.appendChild(b);});tip.hidden=false;}
if(t)place(e);});
document.addEventListener('mouseout',e=>{if(!e.relatedTarget){tip.hidden=true;cur=null;}});
addEventListener('scroll',()=>{tip.hidden=true;cur=null;},{passive:true});
})();"""

# 「备份与恢复」对话框：导出、导入都交给窗口背后的服务（stats_server.py，请求带上地址里的 key）。导出先让用户选存到哪里
# （要在点击后马上调用），再取备份写进去；导入先预览会导入多少，确认后导入、重新载入页面，载入后显示导入了什么
# （stats-flash），导入的成就不再弹解锁提示（stats-quiet，见 LIVE_JS）。页面不是服务给的（直接打开的文件、
# MXU 网页服务）时按钮不能点，说明怎么打开窗口。文件名等都用 textContent 放进页面。
BACKUP_JS = """(()=>{
const $=(s,r=document)=>r.querySelector(s),NS='http://www.w3.org/2000/svg';
const ss=(k,v)=>{try{if(v===undefined)return sessionStorage.getItem(k);if(v===null)sessionStorage.removeItem(k);else sessionStorage.setItem(k,v);}catch(e){}return null;};
const n=x=>(+x||0).toLocaleString();
function ic(name){const s=document.createElementNS(NS,'svg'),u=document.createElementNS(NS,'use');s.setAttribute('class','ic');s.setAttribute('aria-hidden','true');u.setAttribute('href','#'+name);s.appendChild(u);return s;}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
function btn(cls,text,icon){const b=el('button',cls);b.type='button';if(icon)b.appendChild(ic(icon));b.appendChild(document.createTextNode(text));return b;}
function notice(text){const box=el('div','notice'),x=el('button','','×');box.setAttribute('role','status');x.title='关闭';
box.append(ic('u-check'),el('div','',text),x);const close=()=>box.remove();x.onclick=close;setTimeout(close,9000);document.body.appendChild(box);}
const flash=ss('stats-flash');
if(flash){ss('stats-flash',null);notice(flash);}
const dlg=$('dialog.bk');
if(!dlg)return;
const at=new URL(location.href),key=at.searchParams.get('key')||'',via=at.searchParams.get('path');
const ON=location.protocol==='http:'&&!!key&&!via;
const q=s=>$('[data-bk="'+s+'"]',dlg),pv=$('.bk-pv',dlg),box=$('.bk-msg',dlg),file=q('file'),dir=q('dir');
let working=false,src=null,down=null;
function lock(on){working=on;dlg.querySelectorAll('[data-bk]').forEach(c=>{if(c!==file)c.disabled=on||!ON||c.hasAttribute('data-empty');});}
function msg(kind,text){box.replaceChildren();box.hidden=!text;if(!text)return;box.className='bk-msg'+(kind==='wait'?'':' '+kind);
box.append(kind==='wait'?el('span','spin'):ic(kind==='ok'?'u-check':'u-alert'),el('div','',text));}
function reset(){src=null;pv.hidden=true;pv.replaceChildren();msg(null);}
if(!ON){$('.bk-offt',dlg).textContent=via?'这里不能导出、导入：运行任务「打开统计窗口」，或者双击包目录里的「统计与成就」快捷方式，在打开的窗口里操作'
:'这个窗口打开的是页面文件，不能导出、导入：关掉它，重新运行任务「打开统计窗口」或双击包目录里的「统计与成就」快捷方式打开';$('.bk-off',dlg).hidden=false;}
lock(false);
async function call(path,opt){opt=opt||{};opt.headers=Object.assign({'X-Stats-Key':key},opt.headers||{});let r;
try{r=await fetch(path,opt);}catch(e){throw new Error('窗口背后的服务已经停了：关掉这个窗口重新打开再试');}
if(r.ok)return r;let t='';try{t=(await r.json()).error||'';}catch(e){}
throw new Error(t||(r.status===403?'窗口背后的服务换过了：关掉这个窗口重新打开再试':'出错了（'+r.status+'）'));}
function stamp(){const d=new Date(),p=x=>String(x).padStart(2,'0');return d.getFullYear()+p(d.getMonth()+1)+p(d.getDate())+'-'+p(d.getHours())+p(d.getMinutes())+p(d.getSeconds());}
async function exportIt(){
if(working||!ON)return;
let h=null;
if(window.showSaveFilePicker){
try{h=await showSaveFilePicker({suggestedName:'OurNotes-Auto-backup-'+stamp()+'.zip',startIn:'documents',types:[{description:'OurNotes-Auto 备份',accept:{'application/zip':['.zip']}}]});}
catch(e){if(e&&e.name==='AbortError')return;h=null;}}
reset();lock(true);msg('wait','正在导出…');
try{const r=await call('api/export');let info={};try{info=JSON.parse(r.headers.get('X-Backup-Info')||'{}')||{};}catch(e){}
const blob=await r.blob(),what=' '+n(info.records)+' 局演奏记录、'+n(info.sessions)+' 次运行记录、'+n(info.achievements)+' 个成就';
if(h){try{const w=await h.createWritable();await w.write(blob);await w.close();}catch(e){throw new Error('写不了选的文件：'+(e.message||e));}
msg('ok','已导出'+what+'：「'+h.name+'」。换了新的包以后，在新的包的这里导入就能接着统计');}
else{const a=el('a');a.href=URL.createObjectURL(blob);a.download=info.name||'OurNotes-Auto-backup-'+stamp()+'.zip';document.body.appendChild(a);a.click();a.remove();
setTimeout(()=>URL.revokeObjectURL(a.href),60000);msg('ok','已导出'+what+'，交给浏览器下载了：「'+a.download+'」');}
}catch(e){if(h){try{if((await h.getFile()).size===0)await h.remove();}catch(x){}}msg('err',e.message||String(e));}
finally{lock(false);}}
function show(j){pv.replaceChildren();const t=j.total||{},hd=el('div','bk-pvh'),ul=el('ul','bk-st'),act=el('div','bk-act');
hd.append(ic('u-file'),el('b','',j.name||''));
const item=(text,on,label,small)=>{const li=el('li',on?'':'z');li.append(el('b','',text),el('span','',label));if(small)li.append(el('small','',small));ul.append(li);};
item('+'+n(j.records),j.records>0,'局演奏记录',j.known?'另有 '+n(j.known)+' 局这边已经有了':'');
item('+'+n(j.sessions),j.sessions>0,'次运行记录','');
item('+'+n(j.achievements),j.achievements>0,'个成就','');
item(j.offsets?'导入':'不变',!!j.offsets,'学到的 offset',j.offsets?'这边还没学过':'');
pv.append(hd,el('div','bk-pvs','备份里有 '+n(t.records)+' 局演奏记录、'+n(t.sessions)+' 次运行记录、'+n(t.achievements)+' 个成就'),ul);
if(j.bad)pv.append(el('p','bk-note','有 '+n(j.bad)+' 行演奏记录读不了，会跳过'));
if(!j.changed)pv.append(el('p','bk-note','这边都有了，不用导入'));
else if(!j.records&&!j.sessions&&!j.achievements&&!j.offsets)pv.append(el('p','bk-note','只会把成就的解锁时间改早（备份里解锁得更早）'));
if(j.changed){const c=btn('btn','取消'),g=btn('btn pri','确认导入','u-upload');c.dataset.bk='cancel';g.dataset.bk='go';c.onclick=reset;g.onclick=importIt;act.append(c,g);}
else{const o=btn('btn pri','好');o.dataset.bk='ok';o.onclick=reset;act.append(o);}
pv.append(act);pv.hidden=false;pv.scrollIntoView({block:'nearest'});}
async function look(body,headers,label){
if(working)return;reset();src={body,headers};lock(true);msg('wait','正在读取'+label+'…');
try{const r=await call('api/preview',{method:'POST',body,headers});const j=await r.json();msg(null);show(j);}
catch(e){src=null;msg('err',e.message||String(e));}
finally{lock(false);}}
async function importIt(){
if(working||!src)return;lock(true);msg('wait','正在导入…');let done=false;
try{const r=await call('api/import',{method:'POST',body:src.body,headers:src.headers}),j=await r.json();
if(!j.backup){reset();msg('ok','这边都有了，没有改动');return;}
const parts=[];if(j.records)parts.push(n(j.records)+' 局演奏记录');if(j.sessions)parts.push(n(j.sessions)+' 次运行记录');
if(j.achievements)parts.push(n(j.achievements)+' 个成就');if(j.offsets)parts.push('学到的 offset');
ss('stats-flash','已从「'+j.name+'」导入 '+(parts.join('、')||'成就的解锁时间')+'。导入前的记录复制到了 '+j.backup);ss('stats-quiet','1');
done=true;location.reload();}
catch(e){msg('err',e.message||String(e));}
finally{if(!done)lock(false);}}
file.onchange=()=>{const f=file.files&&file.files[0];file.value='';if(!f)return;
if(f.size>BK_MAX){reset();msg('err','「'+f.name+'」太大了（超过 '+Math.round(BK_MAX/1048576)+' MB），不是导出的备份');return;}
look(f,{'Content-Type':'application/zip','X-File-Name':encodeURIComponent(f.name)},'「'+f.name+'」');};
function fromDir(){const p=dir.value.trim();if(!p){dir.focus();msg('err','先填旧的包目录');return;}
look(JSON.stringify({path:p}),{'Content-Type':'application/json'},'「'+p+'」');}
q('export').onclick=exportIt;
q('pick').onclick=()=>{if(!working&&ON)file.click();};
q('dirgo').onclick=fromDir;
dir.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();fromDir();}});
document.addEventListener('click',e=>{if(e.target.closest('[data-bk-open]')&&!dlg.open)dlg.showModal();});
$('[data-bk-close]',dlg).onclick=()=>{if(!working)dlg.close();};
dlg.addEventListener('cancel',e=>{if(working)e.preventDefault();});
dlg.addEventListener('pointerdown',e=>{down=e.target;});
dlg.addEventListener('click',e=>{if(e.target===dlg&&down===dlg&&!working)dlg.close();});
dlg.addEventListener('close',()=>{if(!working)reset();});
})();"""


def _head_js() -> str:
    """<head> 里先定下当前页签（自动重新载入后不会先闪一下概览）。"""
    tabs = json.dumps([k for k, _, _ in TABS])
    return (f"<script>(()=>{{let t='ov';try{{const s=JSON.parse(sessionStorage.getItem('{UI_KEY}'));if(s&&typeof s.tab==='string')t=s.tab}}catch(e){{}}"
            f"if(!{tabs}.includes(t))t='ov';document.documentElement.dataset.tab=t;}})();</script>")


def _tab_css() -> str:
    """按 <html data-tab> 显示对应的页签和高亮的按钮（纯 CSS，脚本还没跑时也对）。"""
    panes = ",".join(f'html[data-tab="{k}"] .pane[data-pane="{k}"]' for k, _, _ in TABS)
    btns = ",".join(f'html[data-tab="{k}"] .tabs [data-t="{k}"]' for k, _, _ in TABS)
    icons = ",".join(f'html[data-tab="{k}"] .tabs [data-t="{k}"] .ic' for k, _, _ in TABS)
    return (f"{panes}{{display:block}}{btns}{{background:var(--card);color:var(--fg);font-weight:600;"
            f"box-shadow:0 1px 3px rgba(0,0,0,.08),0 0 0 1px var(--line)}}{icons}{{color:var(--acc)}}")


# ---------------------------------------------------------------- 小工具


def _e(text: object) -> str:
    return html.escape(str(text))


def _ratio(a: float, b: float) -> float:
    """a / b，限制在 0~1；b 为 0 时为 0。"""
    if not b:
        return 0.0
    return max(0.0, min(1.0, a / b))


def _rate(a: float, b: float, digits: int = 1) -> str:
    """百分比，往下取整（没全 AP 时不会显示成 100%）；b 为 0 时为「-」。"""
    if not b:
        return "-"
    p = math.floor(a / b * 100 * 10**digits + 1e-6) / 10**digits
    return f"{p:.{digits}f}%"


def _w(r: float) -> str:
    """进度条的宽度。"""
    return f"{max(0.0, min(1.0, r)) * 100:.1f}%"


def _val(a: Achievement, t: Totals) -> float:
    """成就的当前进度；出错按 0（统计页出错不影响打歌）。"""
    try:
        return float(a.value(t))
    except Exception:  # noqa: BLE001
        return 0.0


def _title(view: View, mid: int) -> str:
    try:
        return str(view.title(mid))
    except Exception:  # noqa: BLE001 - 曲名表出错时显示 ID
        return str(mid)


def _d(ts: float | None) -> str:
    try:
        return _date(ts) if ts is not None else "-"
    except (OverflowError, OSError, ValueError, TypeError):
        return "-"


def _t(ts: float | None) -> str:
    try:
        return _when(ts) if ts is not None else "-"
    except (OverflowError, OSError, ValueError, TypeError):
        return "-"


def _today(view: View) -> date:
    try:
        return date.fromtimestamp(view.now)
    except (OverflowError, OSError, ValueError, TypeError):
        return date.today()


def _split(text: str) -> tuple[str, str]:
    """「327.6 小时」→（327.6，小时），大数字和单位分开排。"""
    num, _, unit = text.partition(" ")
    return num, unit


def _diff_cls(diff: str) -> tuple[str, int]:
    """难度的样式和排序用的序号（不在 DIFFICULTIES 里的为 -1）。"""
    d = str(diff).lower()
    i = DIFFICULTIES.index(d) if d in DIFFICULTIES else -1
    return (f"d{i}" if i >= 0 else "dx"), i


def _dg(diff: str) -> str:
    cls, _ = _diff_cls(diff)
    return f'<span class="dg {cls}">{_e(str(diff).upper())}</span>'


def _vd(r) -> str:
    v = verdict(r)
    if v == "AP":
        return '<span class="vd ap">AP</span>'
    if v == "FC":
        return '<span class="vd fc">FC</span>'
    return f'<span class="vd no">{_e(v)}</span>' if v else ""


def _tier(a: Achievement) -> str:
    tier = a.tier if a.tier in TIERS else 1
    return f'<span class="tier t{tier}">{_e(TIERS.get(tier, ""))}</span>'


def _badge(a: Achievement, on: bool, size: str = "") -> str:
    tier = a.tier if a.tier in TIERS else 1
    cls = " ".join(x for x in ("badge", f"t{tier}", "" if on else "off", size) if x)
    return f'<span class="{cls}">{_ic(GROUP_ICON.get(a.group, "g-note"), "gi")}</span>'


def _secret_badge(size: str = "") -> str:
    return f'<span class="badge q{" " + size if size else ""}">{_ic("g-q", "gi")}</span>'


def _groups() -> list[str]:
    return list(dict.fromkeys([*GROUPS, *(a.group for a in ACHIEVEMENTS)]))


def _secret(view: View, a: Achievement) -> bool:
    """没解锁的隐藏成就：名字、说明、进度都不能出现在页面上。"""
    return a.hidden and a.id not in view.unlocked


def _goals(view: View, count: int) -> list[Achievement]:
    try:
        return next_goals(view, count)
    except Exception:  # noqa: BLE001
        return [a for a in ACHIEVEMENTS if a.id not in view.unlocked and not a.hidden][:count]


# ---------------------------------------------------------------- 图


def _nice(top: float) -> tuple[float, float]:
    """纵轴的最大值和刻度间隔（1 / 2 / 5 × 10ⁿ，大约 3~4 格）。"""
    if top <= 0:
        return 4.0, 1.0
    raw = top / 4
    mag = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    step = next((m * mag for m in (1, 2, 5, 10) if m * mag * 4 >= top), 10 * mag)
    step = max(1.0, step)
    return math.ceil(top / step) * step, step


def _bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """顶上圆角、底下直角的柱子。"""
    r = max(0.0, min(r, w / 2, h))
    return (f"M{x:.1f} {y + h:.1f}V{y + r:.1f}Q{x:.1f} {y:.1f} {x + r:.1f} {y:.1f}H{x + w - r:.1f}"
            f"Q{x + w:.1f} {y:.1f} {x + w:.1f} {y + r:.1f}V{y + h:.1f}Z")


def _columns(cols: list[tuple[int, int, str]], labels: list[tuple[int, str]], height: int, width: int,
             stacked: bool, now: int = -1, blank: str = "", grow: bool = False) -> str:
    """柱状图：cols 是（总数，其中 AP，悬停提示）。SVG 横向随容器伸缩，刻度文字放在 SVG 外面不变形。
    stacked 时 AP 在下、其余在上，中间留 2px 缝。"""
    n = max(1, len(cols))
    top = max((c[0] for c in cols), default=0)
    mx, step = _nice(top)
    slot = width / n
    bw = min(slot * 0.62, 24.0)
    ticks = [step * i for i in range(int(round(mx / step)) + 1)]
    base = ' class="base"'
    grid = "".join(f'<line{base if v == 0 else ""} x1="0" x2="{width}" y1="{height - v / mx * height:.1f}" y2="{height - v / mx * height:.1f}"/>' for v in ticks)
    body = []
    for i, (total, ap, tip) in enumerate(cols):
        x = i * slot + (slot - bw) / 2
        g = [f'<rect class="hit" x="{i * slot:.1f}" y="0" width="{slot:.1f}" height="{height}"/>']
        if total > 0:
            h = max(2.0, total / mx * height)
            y = height - h
            ha = min(h, max(0, min(ap, total)) / mx * height) if stacked else h
            rest = h - ha
            if stacked and rest >= 3 and ha >= 1:
                g.append(f'<path class="c-rest" d="{_bar_path(x, y, bw, rest - 2)}"/>')
                g.append(f'<rect class="c-ap" x="{x:.1f}" y="{height - ha:.1f}" width="{bw:.1f}" height="{ha:.1f}"/>')
            elif stacked and ha < 1:
                g.append(f'<path class="c-rest" d="{_bar_path(x, y, bw, h)}"/>')
            else:
                g.append(f'<path class="c-ap" d="{_bar_path(x, y, bw, h)}"/>')
        body.append(f'<g class="col{" now" if i == now else ""}" data-tip="{_e(tip)}">{"".join(g)}</g>')
    ylab = "".join(f'<span style="bottom:{v / mx * 100:.2f}%">{v:,.0f}</span>' for v in ticks)
    xlab = "".join(f'<span style="left:{(i + 0.5) / n * 100:.2f}%">{_e(text)}</span>' for i, text in labels)
    empty = f'<div class="blank">{_e(blank)}</div>' if blank and top <= 0 else ""
    return (f'<div class="chart{" grow" if grow else ""}" style="--h:{height}px"><div class="yax">{ylab}</div><div class="plot">'
            f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none"><g class="grid">{grid}</g>{"".join(body)}</svg>{empty}</div>'
            f'<div class="xax">{xlab}</div></div>')


def _days_chart(view: View, height: int, width: int, days: int = 30, grow: bool = False) -> str:
    t = view.totals
    today = _today(view)
    span = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    cols = []
    for d in span:
        n, ap = int(t.per_day.get(d, 0)), int(t.ap_per_day.get(d, 0))
        when = "今天" if d == today else f"{d:%m-%d} 周{WEEK[d.weekday()]}"
        tip = f"{when}\n{n:,} 局" + (f" · AP {min(ap, n):,}（{_rate(ap, n)}）" if n else "")
        cols.append((n, ap, tip))
    marks = sorted({0, days // 4, days // 2, days * 3 // 4})
    labels = [(i, f"{span[i]:%m-%d}") for i in marks if i < days - 3] + [(days - 1, "今天")]
    return _columns(cols, labels, height, width, True, days - 1, "这 30 天没有演奏", grow)


def _legend() -> str:
    return ('<span class="legend"><span><i style="background:var(--ap)"></i>ALL PERFECT</span>'
            '<span><i style="background:var(--rest)"></i>其他</span></span>')


def _ring(view: View) -> str:
    """成就环：每个成就一小段，解锁的按档位上色（铜→传说），没解锁的灰色。"""
    done = sorted((a for a in ACHIEVEMENTS if a.id in view.unlocked), key=lambda a: (a.tier, view.unlocked.get(a.id, 0)))
    todo = [a for a in ACHIEVEMENTS if a.id not in view.unlocked]
    n = max(1, len(ACHIEVEMENTS))
    c = 2 * math.pi * 52
    seg = c / n
    gap = min(1.8, seg * 0.3)
    arcs = []
    for i, a in enumerate([*done, *todo]):
        cls = f"s{a.tier if a.tier in TIERS else 1}" if a.id in view.unlocked else "s0"
        arcs.append(f'<circle class="sg {cls}" cx="60" cy="60" r="52" stroke-dasharray="{seg - gap:.2f} {c - seg + gap:.2f}" stroke-dashoffset="{-i * seg:.2f}"/>')
    got = len(done)
    return (f'<div class="ring"><svg viewBox="0 0 120 120">{"".join(arcs)}</svg>'
            f'<div class="c"><b>{got}<small>/ {len(ACHIEVEMENTS)}</small></b><span>已解锁 {_rate(got, len(ACHIEVEMENTS), 0)}</span></div></div>')


# ---------------------------------------------------------------- 概览


def _live_bar(view: View) -> str:
    """正在运行的任务：先写页面生成时的情况，之后由脚本就地更新（见 :data:`LIVE_JS`），不重新载入。"""
    out = []
    for s in view.live:
        now = view.doing.get(live_key(s)) or {}
        upd = now.get("upd")
        stale = upd is not None and view.now - upd > LIVE_STALE_S  # 进程被强行结束：时间停在最后一次更新
        end = min(view.now, upd) if stale else view.now
        since = now.get("since") if isinstance(now.get("since"), (int, float)) else s.start
        items = [("已进行", duration(max(0.0, end - s.start))), ("本次演奏", f"{s.plays:,} 局"),
                 ("AP", f"{s.ap:,}（{_rate(s.ap, s.plays)}）" if s.plays else "0")]
        if s.lb_wait_s:
            items.append(("等 LB", duration(s.lb_wait_s)))
        if s.skips:
            items.append(("跳过", f"{s.skips:,} 次"))
        cells = "".join(f'<span class="li{" el" if k == "已进行" else ""}">{_e(k)}<b>{_e(v)}</b></span>' for k, v in items)
        out.append(f'<div class="live{" stale" if stale else ""}" data-key="{_e(live_key(s))}" data-start="{s.start:.1f}">'
                   f'<span class="lt"><span class="pulse"></span>{_e(s.task or "打歌任务")} · 进行中</span>'
                   f'<span class="li">{_e(_t(s.start))} 开始</span>{cells}'
                   f'<button class="lnk" data-go="runs">运行记录{_ic("u-right")}</button>'
                   f'<div class="lnow"{" hidden" if not now.get("kind") else ""}>{_live_now(now, since, end, stale, view.now)}</div></div>')
    return "".join(out)


def _live_now(now: dict, since: float, end: float, stale: bool, now_ts: float) -> str:
    """实时状态那一行（在下面一行）：图标、在做什么、说明，然后是已多久。"""
    label, icon = LIVE_KINDS.get(str(now.get("kind")), ("进行中", "u-play"))
    bits = []
    if stale:
        bits.append(f"超过 {max(1, round((now_ts - now.get('upd', since)) / 60))} 分钟没有更新，可能已经停止")
    else:
        bits.append(_ago(end - since))
        eta = now.get("eta")
        if isinstance(eta, (int, float)) and eta > end:
            left = int(eta - end)
            bits.append(f"下一个 LB {left // 60}:{left % 60:02d} 后恢复")
        daily = now.get("daily")
        if isinstance(daily, (int, float)) and daily > end:
            bits.append(f"下次领日常 {_hm(daily)}")
    detail = str(now.get("detail") or "")
    d = f'<span class="ld">{_e(detail)}</span>' if detail else '<span class="ld"></span>'
    return f'{_ic(icon, "ic ls")}<span class="lk">{_e(label)}</span>{d}<span class="lx">{_e(" · ".join(bits))}</span>'


def _ago(seconds: float) -> str:
    """在做的这件事做了多久（不到一分钟时不写「0 分钟」）。"""
    return "刚开始" if seconds < 60 else f"已 {duration(seconds)}"


def _hm(ts: float) -> str:
    """时:分（本地时间）。"""
    try:
        return time.strftime("%H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError, TypeError):
        return "-"


def _ach_card(view: View) -> str:
    rows = []
    for tier, name in TIERS.items():
        items = [a for a in ACHIEVEMENTS if a.tier == tier]
        got = sum(a.id in view.unlocked for a in items)
        rows.append(f'<li class="t{tier}"><span class="badge xs t{tier}{"" if got else " off"}"></span><span class="tn">{_e(name)}</span>'
                    f'<span class="bar"><i style="width:{_w(_ratio(got, len(items)))}"></i></span><span class="tc">{got} / {len(items)}</span></li>')
    return (f'<section class="card achcard">{_ring(view)}<div class="ai"><div class="ak">成就进度 · 按档位</div>'
            f'<ul class="tiers">{"".join(rows)}</ul><button class="lnk" data-go="ach">查看全部成就{_ic("u-right")}</button></div></section>')


def _tile(icon: str, key: str, value: str, sub: str = "", ratio: float | None = None) -> str:
    """数字卡片；sub 是已经转义过的 HTML。"""
    num, unit = _split(value)
    bar = f'<span class="bar"><i style="width:{_w(ratio)}"></i></span>' if ratio is not None else ""
    small = f"<small>{_e(unit)}</small>" if unit else ""
    return (f'<div class="card tile"><div class="tk">{_ic(icon)}{_e(key)}</div><div class="tv">{_e(num)}{small}</div>'
            f'<div class="ts">{sub}</div>{bar}</div>')


def _tiles(view: View) -> str:
    t = view.totals
    days = len(t.per_day)
    run_sub = (f"其中挂机 {duration(t.idle_s)}" if t.idle_s else f"{t.runs:,} 次运行" if t.runs
               else "按演奏记录估算" if t.estimated_s else "从运行打歌任务开始算")
    pending = len(t.charts) - t.charts_ap
    if pending > 0:
        chart_sub = f'<button class="lnk" data-go="charts" data-na="1">还有 {pending:,} 张没 AP{_ic("u-right")}</button>'
    else:
        chart_sub = "打过的谱面都 AP 了" if t.charts else "还没有打过的谱面"
    tiles = [
        _tile("u-clock", "累计运行", duration(t.run_s), _e(run_sub)),
        _tile("u-play", "演奏", f"{t.plays:,} 局", _e(f"{days:,} 天 · 日均 {t.plays / days:,.0f} 局" if days else "还没有演奏")),
        _tile("u-star", "ALL PERFECT", f"{t.ap:,} 局", _e(f"AP 率 {_rate(t.ap, t.plays)}"), _ratio(t.ap, t.plays)),
        _tile("u-check", "FULL COMBO", f"{t.fc:,} 局", _e(f"FC 率 {_rate(t.fc, t.plays)}"), _ratio(t.fc, t.plays)),
        _tile("u-flame", "最长连续 AP", f"{t.best_streak:,} 局", _e(f"当前连续 {t.streak:,} 局")),
        _tile("u-layers", "AP 过的谱面", f"{t.charts_ap:,} / {len(t.charts):,} 张", chart_sub, _ratio(t.charts_ap, len(t.charts))),
    ]
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _goal_row(view: View, a: Achievement) -> str:
    v = _val(a, view.totals)
    r = _ratio(v, a.target)
    return (f'<div class="goal" data-a="{_e(a.id)}">{_badge(a, False, "sm")}<div class="gb">'
            f'<div class="gn"><span>{_e(a.name)}</span>{_tier(a)}<b class="pct">{math.floor(r * 100)}%</b></div>'
            f'<div class="gd">{_e(a.desc)}</div>'
            f'<div class="pb"><span class="bar"><i style="width:{_w(r)}"></i></span><span>{_e(progress_text(a, v))}</span></div></div></div>')


def _goals_card(view: View) -> str:
    goals = _goals(view, 3)
    body = "".join(_goal_row(view, a) for a in goals) or '<div class="none">所有成就都解锁了</div>'
    return f'<section class="card"><h3 class="h">{_ic("u-target")}下一个目标<span class="sub">离达成最近的</span></h3>{body}</section>'


def _unlocks_card(view: View) -> str:
    rows = []
    for a in recent_unlocked(view, 5):
        when = view.unlocked.get(a.id)
        fresh = '<span class="new">NEW</span>' if when is not None and view.now - when < 86400 else ""
        rows.append(f'<div class="goal" data-a="{_e(a.id)}">{_badge(a, True, "sm")}<div class="gb">'
                    f'<div class="gn"><span>{_e(a.name)}</span>{fresh}</div><div class="gd">{_e(a.desc)}</div></div>'
                    f'<span class="when">{_tier(a)}<br>{_e(_d(when)[5:] or "-")}</span></div>')
    body = "".join(rows) or '<div class="none">还没有解锁的成就</div>'
    return f'<section class="card"><h3 class="h">{_ic("u-spark")}最近解锁</h3>{body}</section>'


def _wall(view: View) -> str:
    groups = []
    for g in _groups():
        items = [a for a in ACHIEVEMENTS if a.group == g]
        if not items:
            continue
        badges = []
        for a in items:
            when = view.unlocked.get(a.id)
            if _secret(view, a):
                badges.append(f'<span class="wb" data-go="ach" data-tip="{_e("???" + chr(10) + secret_text(a))}">{_secret_badge("sm")}</span>')
                continue
            tier = TIERS.get(a.tier, "")
            if when is not None:
                tip = f"{a.name} · {tier}\n{a.desc}\n{_d(when)} 解锁"
            else:
                tip = f"{a.name} · {tier}\n{a.desc}\n{progress_text(a, _val(a, view.totals))}"
            badges.append(f'<span class="wb" data-a="{_e(a.id)}" data-tip="{_e(tip)}">{_badge(a, when is not None, "sm")}</span>')
        got = sum(a.id in view.unlocked for a in items)
        groups.append(f'<div class="wg"><div class="wgk">{_e(g)}<b>{got}/{len(items)}</b></div><div class="wgb">{"".join(badges)}</div></div>')
    return (f'<section class="card wall mt"><h3 class="h">{_ic("u-medal")}徽章墙<span class="sub">悬停看详情，点一下打开成就页</span>'
            f'<span class="r"><button class="lnk" data-go="ach">全部成就{_ic("u-right")}</button></span></h3>'
            f'<div class="wallin">{"".join(groups)}</div></section>')


ART = ('<svg class="art" viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="52" fill="url(#bg)" opacity=".14"/>'
       '<circle cx="60" cy="60" r="38" fill="url(#bg)"/><circle cx="60" cy="60" r="30" fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="1.5"/>'
       '<path fill="#fff" d="M66 38v26.5a8 8 0 1 1-4-6.9V44h-10v-6z" transform="translate(-2 2)"/>'
       '<path fill="#e4508f" d="M98 22q1.4 7.6 9 9-7.6 1.4-9 9-1.4-7.6-9-9 7.6-1.4 9-9z"/><path fill="#7c5cff" d="M20 84q1 5 6 6-5 1-6 6-1-5-6-6 5-1 6-6z"/></svg>')


def _empty_card() -> str:
    return (f'<section class="card empty">{ART}<div><h2>还没有演奏记录</h2>'
            f'<p>在界面里运行打歌任务（重复刷歌、挂机、AP补完……），每打完一局这里会自动刷新；下面的成就就是第一批目标。</p>'
            f'<ul><li>累计运行时长</li><li>ALL PERFECT / FULL COMBO</li><li>每天的局数</li><li>每张谱面的战绩</li><li>{len(ACHIEVEMENTS)} 个成就</li></ul></div></section>')


def _starter(view: View) -> str:
    """没有记录时：每组第一个成就作为起步目标。"""
    picks = []
    for g in _groups():
        a = next((a for a in ACHIEVEMENTS if a.group == g and not a.hidden and a.id not in view.unlocked), None)
        if a:
            picks.append(a)
    body = "".join(_goal_row(view, a) for a in picks)
    return f'<section class="card mt"><h3 class="h">{_ic("u-target")}第一批目标<span class="sub">每组先从这个开始</span></h3><div class="goals3">{body}</div></section>'


def _backup_note(view: View) -> str:
    return (f'<div class="note mt">{_ic("u-archive")}<div>{_e(backup_text(view))}。'
            f'<button class="lnk" type="button" data-bk-open>打开备份与恢复{_ic("u-right")}</button></div></div>')


def _overview(view: View) -> str:
    t = view.totals
    if not (t.plays or t.runs):
        return f'{_live_bar(view)}<div class="hero">{_ach_card(view)}{_empty_card()}</div>{_starter(view)}{_wall(view)}{_backup_note(view)}'
    today = _today(view)
    n_today = int(t.per_day.get(today, 0))
    n7 = sum(int(t.per_day.get(today - timedelta(days=i), 0)) for i in range(7))
    chart = (f'<section class="card fx"><h3 class="h">{_ic("u-chart")}最近 30 天<span class="sub">今天 {n_today:,} 局 · 近 7 天 {n7:,} 局</span>'
             f'<span class="r">{_legend()}</span></h3>{_days_chart(view, 200, 600, grow=True)}</section>')
    return (f'{_live_bar(view)}<div class="hero">{_ach_card(view)}{_tiles(view)}</div>'
            f'<div class="row3">{chart}{_goals_card(view)}{_unlocks_card(view)}</div>{_wall(view)}{_backup_note(view)}')


# ---------------------------------------------------------------- 成就


def _ach_item(view: View, a: Achievement) -> str:
    if _secret(view, a):
        return (f'<div class="ach todo secret" data-st="todo" data-tier="0">{_secret_badge()}<div class="ab">'
                f'<div class="an">???</div><div class="ad">{_e(secret_text(a))}</div></div></div>')
    when = view.unlocked.get(a.id)
    tier = a.tier if a.tier in TIERS else 1
    hid = '<span class="hid">隐藏</span>' if a.hidden else ""
    head = f'<div class="an">{_e(a.name)}{hid}{_tier(a)}</div><div class="ad">{_e(a.desc)}</div>'
    if when is not None:
        foot = f'<div class="af">{_ic("u-check")}{_e(_d(when))} 解锁</div>'
        state = "done"
    else:
        v = _val(a, view.totals)
        r = _ratio(v, a.target)
        foot = (f'<div class="pb"><span class="bar"><i style="width:{_w(r)}"></i></span>'
                f'<span>{_e(progress_text(a, v))}</span></div>')
        state = "todo"
    return (f'<div class="ach {state} t{tier}" id="a-{_e(a.id)}" data-st="{state}" data-tier="{tier}">'
            f'{_badge(a, when is not None)}<div class="ab">{head}{foot}</div></div>')


def _achievements(view: View) -> str:
    total = len(ACHIEVEMENTS)
    got = sum(a.id in view.unlocked for a in ACHIEVEMENTS)
    st = [("all", "全部", total), ("done", "已解锁", got), ("todo", "未解锁", total - got)]
    st_chips = "".join(f'<button class="chip" data-v="{k}">{name}<i>{n}</i></button>' for k, name, n in st)
    groups = _groups()
    g_chips = ['<button class="chip" data-v="all">全部</button>']
    for g in groups:
        items = [a for a in ACHIEVEMENTS if a.group == g]
        g_chips.append(f'<button class="chip" data-v="{_e(g)}">{_ic(GROUP_ICON.get(g, "g-note"), "ic gi2")}{_e(g)}'
                       f'<i>{sum(a.id in view.unlocked for a in items)}/{len(items)}</i></button>')
    t_chips = ['<button class="chip" data-v="all">全部</button>']
    for tier, name in TIERS.items():
        items = [a for a in ACHIEVEMENTS if a.tier == tier]
        t_chips.append(f'<button class="chip" data-v="{tier}"><span class="badge xs t{tier}"></span>{_e(name)}'
                       f'<i>{sum(a.id in view.unlocked for a in items)}/{len(items)}</i></button>')
    bar = (f'<section class="card achbar">{_ring(view)}<div class="filters">'
           f'<div class="frow"><span class="flab">状态</span><div class="chips" data-af="ast">{st_chips}</div></div>'
           f'<div class="frow"><span class="flab">分组</span><div class="chips" data-af="ag">{"".join(g_chips)}</div></div>'
           f'<div class="frow"><span class="flab">档位</span><div class="chips" data-af="at">{"".join(t_chips)}</div></div>'
           f'</div></section>')
    sections = []
    for g in groups:
        items = [a for a in ACHIEVEMENTS if a.group == g]
        if not items:
            continue
        n = sum(a.id in view.unlocked for a in items)
        cards = "".join(_ach_item(view, a) for a in items)
        sections.append(f'<section class="agroup" data-g="{_e(g)}"><h3 class="agh">{_ic(GROUP_ICON.get(g, "g-note"), "ic")}{_e(g)}'
                        f'<span class="n">{n} / {len(items)}</span><span class="bar"><i style="width:{_w(_ratio(n, len(items)))}"></i></span></h3>'
                        f'<div class="agrid">{cards}</div></section>')
    return f'{bar}{"".join(sections)}<div class="none" id="ach-none" hidden>没有符合条件的成就</div>'


# ---------------------------------------------------------------- 演奏数据


def _fig(key: str, value: str, sub: str = "") -> str:
    num, unit = _split(value)
    small = f"<small>{_e(unit)}</small>" if unit else ""
    return f'<div class="fig"><div class="fk">{_e(key)}</div><div class="fv">{_e(num)}{small}</div><div class="fs">{_e(sub) or "&nbsp;"}</div></div>'


def _hours_chart(view: View) -> str:
    t = view.totals
    total = sum(int(t.per_hour.get(h, 0)) for h in range(24))
    cols = [(int(t.per_hour.get(h, 0)), 0, f"{h:02d}:00 – {h + 1:02d}:00\n{int(t.per_hour.get(h, 0)):,} 局（{_rate(t.per_hour.get(h, 0), total)}）") for h in range(24)]
    labels = [(h, f"{h} 时") for h in (0, 6, 12, 18)] + [(23, "23 时")]
    return _columns(cols, labels, 168, 560, False, blank="还没有演奏")


def _play_data(view: View) -> str:
    t = view.totals
    if not t.plays:
        return f'<section class="card empty">{ART}<div><h2>还没有演奏数据</h2><p>打完第一局后，这里会显示每天的局数、各难度的 AP 率、一天里的时段分布和打得最多的谱面。</p></div></section>'
    days = len(t.per_day)
    best_day = f"{t.best_day_date:%m-%d}" if t.best_day_date else ""
    strip = "".join([
        _fig("音符", f"{t.notes:,} 个", f"PERFECT {_rate(t.perfect, t.notes, 2)}"),
        _fig("累计分数", f"{t.score:,}", f"每局平均 {t.score / t.plays:,.0f}"),
        _fig("一局最大连击", f"{t.max_combo:,}"),
        _fig("单日最多", f"{t.best_day:,} 局", best_day),
        _fig("演奏天数", f"{days:,} 天", f"最长连续 {t.best_day_streak:,} 天"),
        _fig("四个难度都 AP", f"{t.songs_all_ap:,} 首", f"打过 {len({m for m, _ in t.charts}):,} 首歌"),
    ])
    today = _today(view)
    n30 = sum(int(t.per_day.get(today - timedelta(days=i), 0)) for i in range(30))
    a30 = sum(int(t.ap_per_day.get(today - timedelta(days=i), 0)) for i in range(30))
    days_card = (f'<section class="card mt"><h3 class="h">{_ic("u-chart")}每天的局数<span class="sub">最近 30 天共 {n30:,} 局 · AP 率 {_rate(a30, n30)}</span>'
                 f'<span class="r">{_legend()}</span></h3>{_days_chart(view, 220, 1100)}</section>')
    diff_rows = ['<div class="drow hd"><span>难度</span><span>局数</span><span class="v">AP 率</span><span class="v">AP 过的谱面</span></div>']
    rows = _diff_rows(t)
    most = max((r[1] for r in rows), default=0)
    for d, n, ap, c, cap in rows:
        diff_rows.append(f'<div class="drow">{_dg(d)}<span class="dur"><span class="bar" style="flex:1;width:auto"><i style="width:{_w(_ratio(n, most))}"></i></span>'
                         f'<span>{n:,}</span></span><span class="v">{_rate(ap, n)}</span><span class="v">{cap:,}<small>/ {c:,} 张</small></span></div>')
    diff_card = f'<section class="card"><h3 class="h">{_ic("u-layers")}各难度</h3><div class="drows">{"".join(diff_rows)}</div></section>'
    hours_card = (f'<section class="card"><h3 class="h">{_ic("u-clock")}一天里的时段<span class="sub">每个整点开始的一小时里打了多少局</span></h3>'
                  f'{_hours_chart(view)}</section>')
    top = sorted(t.charts.items(), key=lambda kc: (-kc[1].plays, -kc[1].last))[:10]
    most = top[0][1].plays if top else 0
    trs = []
    for i, ((mid, diff), c) in enumerate(top, 1):
        name = _title(view, mid)
        trs.append(f'<div class="tr"><span class="rk">{i}</span><span class="nm" title="{_e(name)}">{_e(name)}</span>{_dg(diff)}'
                   f'<span class="bar"><i style="width:{_w(_ratio(c.plays, most))}"></i></span><span class="v">{c.plays:,} 局</span></div>')
    top_card = f'<section class="card"><h3 class="h">{_ic("u-flame")}打得最多的谱面</h3><div class="top10">{"".join(trs)}</div></section>'
    facts = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in _facts(view))
    facts_card = f'<section class="card"><h3 class="h">{_ic("u-spark")}更多数据</h3><dl class="facts">{facts}</dl></section>'
    return (f'<section class="card strip">{strip}</section>{days_card}'
            f'<div class="grid2">{diff_card}{hours_card}</div><div class="grid2">{top_card}{facts_card}</div>')


# ---------------------------------------------------------------- 谱面

UNPLAYED_SHOWN = 120  # 「曲库里还没打过的歌」最多列出几首


def _by_song(t: Totals) -> dict[int, dict[str, ChartStats]]:
    """打过的谱面按曲目分组（难度转成小写），最近打过的歌在前。"""
    songs: dict[int, dict[str, ChartStats]] = {}
    for (mid, diff), c in sorted(t.charts.items(), key=lambda kc: -kc[1].last):
        songs.setdefault(mid, {})[str(diff).lower()] = c
    return songs


def _level(view: View, mid: int, diff: str) -> int | None:
    lv = view.library.levels.get((mid, diff))
    return lv if isinstance(lv, int) else None


def _best_text(c: ChartStats) -> str:
    if c.best is None:
        return "最好的一局：结果没读全"
    return f"最好的一局：{judgements(c.best)}" + ("" if c.ap else f"（离 AP 差 {_misses(c.best)} 个）")


def _diff_cell(view: View, mid: int, i: int, c: ChartStats | None) -> str:
    """一首歌的一个难度：等级（AP 了是实心的），AP / FC 或者离 AP 差几个，局数。"""
    d = DIFFICULTIES[i]
    lv = _level(view, mid, d)
    name = f"{d.upper()} {lv} 级" if lv is not None else d.upper()
    pill = str(lv) if lv is not None else d[0].upper()
    if c is None:
        if lv is None:
            return f'<td class="dc c{i}" data-v="-1"><span class="muted">–</span></td>'
        return (f'<td class="dc c{i}" data-v="{lv * 10}" data-tip="{_e(name + chr(10) + "还没打过")}"><span class="dcl">'
                f'<span class="lv d{i} none">{pill}</span><span class="ds"><span class="muted">没打过</span></span></span></td>')
    if c.ap:
        rank, state, head = 3, " ap", '<span class="vd ap">AP</span>'
    else:
        gap = f"差 {_misses(c.best)}" if c.best is not None else "未 AP"
        rank, state = (2, " fc") if c.fc else (1, "")
        head = ('<span class="vd fc">FC</span> ' if c.fc else "") + f'<span class="gap">{gap}</span>'
    tip = (name, f"{c.plays:,} 局 · AP {c.ap:,} · FC {c.fc:,} · AP 率 {_rate(c.ap, c.plays)}", _best_text(c), f"最近：{_t(c.last)}")
    todo = "" if c.ap else " todo"
    return (f'<td class="dc c{i}{todo}" data-v="{(lv or 0) * 10 + rank}" data-tip="{_e(chr(10).join(tip))}"><span class="dcl">'
            f'<span class="lv d{i}{state}">{pill}</span><span class="ds"><span>{head}</span><small>{c.plays:,} 局</small></span></span></td>')


def _diff_card(view: View, mid: int, diff: str, c: ChartStats | None) -> str:
    """展开后一个难度的卡片。"""
    lv = _level(view, mid, diff)
    head = _dg(diff) + (f'<span class="lvt">{lv} 级</span>' if lv is not None else "")
    if c is None:
        return f'<div class="dcard none"><div class="dch">{head}</div><div class="muted">还没打过</div></div>'
    tag = '<span class="vd ap">AP</span>' if c.ap else '<span class="vd fc">FC</span>' if c.fc else ""
    if c.best is None:
        best = '<span class="muted">结果没读全</span>'
    else:
        best = f'<span class="jd">{_e(judgements(c.best))}</span>' + ("" if c.ap else f' <span class="gap">差 {_misses(c.best)}</span>')
    rate = _ratio(c.ap, c.plays)
    return (f'<div class="dcard"><div class="dch">{head}{tag}</div>'
            f'<div class="dcn">{c.plays:,} 局 · AP {c.ap:,} · FC {c.fc:,}</div>'
            f'<div class="dcr"><span class="rate"><i style="width:{_w(rate)}"></i></span>AP 率 {_rate(c.ap, c.plays)}</div>'
            f'<dl><dt>最好</dt><dd>{best}</dd><dt>最近</dt><dd>{_e(_t(c.last))}</dd></dl></div>')


def _song(view: View, mid: int, charts: dict[str, ChartStats]) -> str:
    """一首歌一组（<tbody>）：一行四个难度，点开是每个难度的卡片。"""
    name = _title(view, mid)
    band = view.library.bands.get(mid) or ""
    extra = [d for d in charts if d not in DIFFICULTIES]
    played = "".join(str(i) for i, d in enumerate(DIFFICULTIES) if d in charts) + ("x" if extra else "")
    todo = "".join(str(i) for i, d in enumerate(DIFFICULTIES) if d in charts and not charts[d].ap)
    todo += "x" if any(not charts[d].ap for d in extra) else ""
    known = [d for d in DIFFICULTIES if d in charts or _level(view, mid, d) is not None] + extra
    done = sum(1 for d in known if d in charts and charts[d].ap)
    plays = sum(c.plays for c in charts.values())
    ap = sum(c.ap for c in charts.values())
    last = max(c.last for c in charts.values())
    sub = " · ".join(s for s in (band, f"{done} / {len(known)} 张 AP") if s)
    cells = "".join(_diff_cell(view, mid, i, charts.get(d)) for i, d in enumerate(DIFFICULTIES))
    cards = "".join(_diff_card(view, mid, d, charts.get(d)) for d in known)
    rate = _ratio(ap, plays)
    return (f'<tbody data-m="{_e(mid)}" data-k="{_e(f"{name} {mid} {band}".lower())}" data-p="{played}" data-n="{todo}">'
            f'<tr class="sr" tabindex="0" aria-expanded="false"><td class="t" data-v="{_e(name)}"><span class="sn">{_ic("u-right", "ic chev")}'
            f'<span><span class="nm">{_e(name)}</span><small>{_e(sub)}</small></span></span></td>{cells}'
            f'<td class="num">{plays:,}</td><td data-v="{rate:.4f}"><span class="rate"><i style="width:{_w(rate)}"></i></span>{_rate(ap, plays)}</td>'
            f'<td data-v="{last:.0f}">{_e(_t(last))}</td></tr>'
            f'<tr class="sd"><td colspan="{len(DIFFICULTIES) + 4}"><div class="dcards">{cards}</div></td></tr></tbody>')


def _charts_tab(view: View) -> str:
    t = view.totals
    if not t.charts:
        return f'<section class="card empty">{ART}<div><h2>还没有打过的谱面</h2><p>每首歌四个难度的等级、AP / FC、局数、最好的一局会列在这里，可以搜索、排序、只看还没 AP 的。</p></div></section>'
    songs = _by_song(t)
    counts = {str(i): sum(1 for charts in songs.values() if d in charts) for i, d in enumerate(DIFFICULTIES)}
    counts["x"] = sum(1 for charts in songs.values() if any(d not in DIFFICULTIES for d in charts))
    pending = sum(1 for c in t.charts.values() if not c.ap)
    seg = ['<button data-v="all">全部</button>']
    seg += [f'<button data-v="{i}">{_e(DIFFICULTIES[i].upper())}<i>{counts[str(i)]:,}</i></button>'
            for i in range(len(DIFFICULTIES) - 1, -1, -1) if counts[str(i)]]
    if counts["x"]:
        seg.append(f'<button data-v="x">其他<i>{counts["x"]:,}</i></button>')
    cols = [("t", "曲目", ""), *(("d", d.upper(), f' class="c{i}"') for i, d in enumerate(DIFFICULTIES)),
            ("n", "局数", ' class="num"'), ("r", "AP 率", ""), ("w", "最近", "")]
    head = "".join(f'<th data-s="{s}"{cls}>{_e(name)}</th>' for s, name, cls in cols)
    n = len(songs)
    toolbar = (f'<div class="tbar"><label class="search">{_ic("u-search")}<input id="q" type="search" placeholder="搜索曲名、ID 或乐队" autocomplete="off"></label>'
               f'<div class="seg" data-cf>{"".join(seg)}</div>'
               f'<button class="switch" id="notap" role="switch" aria-checked="false"><span class="k"></span>只看还没 AP 的<b>{pending:,} 张</b></button>'
               f'<span class="count" id="cnt">显示 {n:,} / {n:,} 首</span></div>')
    bodies = "".join(_song(view, mid, charts) for mid, charts in songs.items())
    out = (f'<section class="card tblcard">{toolbar}<div class="tw"><table id="charts" data-cd="all"><thead><tr>{head}</tr></thead>'
           f'{bodies}</table><div class="none" id="charts-none" hidden>没有符合条件的歌</div></div></section>')
    rest = [mid for mid in view.library.titles if mid not in songs]
    if rest:
        names = "、".join(_e(_title(view, mid)) for mid in rest[:UNPLAYED_SHOWN])
        more = f" 等 {len(rest):,} 首" if len(rest) > UNPLAYED_SHOWN else ""
        out += (f'<section class="card mt"><div class="note">{_ic("u-music")}<div><b>曲库里还没打过的 {len(rest):,} 首</b>'
                f'<div class="muted">{names}{more}</div></div></div></section>')
    return out


def _misses(r) -> int:
    return sum((x or 0) for x in (r.great, r.good, r.bad, r.miss))


# ---------------------------------------------------------------- 运行记录

PLAYS_MAX = 30_000  # 「全部演奏」最多带上这么多局（页面里每局约 50 字节）
_VERDICT = {"FC": 1, "AP": 2, "结果没读全": 3}  # 和 PAGE_JS 里的 V 对应


def _session_row(s: Session, longest: float, live: bool) -> str:
    tag = '<span class="ltag"><span class="pulse"></span>运行中</span>' if live else ""
    task = f'{_e(s.task or "打歌任务")}{" · 挂机" if s.idle and s.task != "挂机" else ""}'
    start, end = _t(s.start), _t(s.end)
    if len(end) > 6 and start[:5] == end[:5]:  # 同一天只写结束的时刻
        end = end[6:]
    lb = duration(s.lb_wait_s) if s.lb_wait_s else "-"
    on = ' class="on"' if live else ""
    return (f'<tr{on}><td>{_e(start)} – {_e(end)}</td><td>{task}{tag}</td>'
            f'<td><span class="dur"><span class="bar"><i style="width:{_w(_ratio(s.seconds, longest))}"></i></span>{_e(duration(s.seconds))}</span></td>'
            f'<td class="num">{s.plays:,}</td><td class="num">{s.ap:,} <span class="muted">{_rate(s.ap, s.plays)}</span></td>'
            f'<td class="num">{_e(lb)}</td><td class="num">{s.skips:,}</td></tr>')


def _pager(key: str, total: int, unit: str) -> str:
    """翻页条：PAGE_JS 按 data-ps 分页，当前页存在界面状态里（自动重新载入后还在这一页）。"""
    pages = max(1, math.ceil(total / PAGE_SIZE))
    return (f'<div class="pager" data-pager="{key}" data-ps="{PAGE_SIZE}" data-n="{total}" data-u="{unit}"{"" if pages > 1 else " hidden"}>'
            f'<button data-p="first" title="第一页" aria-label="第一页">{_ic("u-first")}</button>'
            f'<button data-p="prev">{_ic("u-left")}上一页</button><span class="pi">第 1 / {pages} 页 · 共 {total:,} {unit}</span>'
            f'<button data-p="next">下一页{_ic("u-right")}</button>'
            f'<button data-p="last" title="最后一页" aria-label="最后一页">{_ic("u-last")}</button></div>')


def _plays_json(view: View, plays: Sequence) -> str:
    """「全部演奏」的数据，页面脚本按页生成表格行。每局 [时间, 曲目, 难度, P, G, g, B, M, 结果, 分数]，
    难度是 DIFFICULTIES 的序号（不认识的难度直接写名字），曲名另外按曲目 ID 存一份；``<`` 转义后放进 <script> 也安全。"""
    titles: dict[int, str] = {}
    rows = []
    for r in plays:
        if r.music_id not in titles:
            titles[r.music_id] = _title(view, r.music_id)
        d = str(r.difficulty).lower()
        rows.append([round(r.timestamp), r.music_id, DIFFICULTIES.index(d) if d in DIFFICULTIES else str(r.difficulty),
                     r.perfect, r.great, r.good, r.bad, r.miss, _VERDICT.get(verdict(r), 0), r.score])
    data = {"d": list(DIFFICULTIES), "t": titles, "r": rows}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _runs(view: View) -> str:
    t = view.totals
    parts = []
    if t.runs or view.live:
        figs = [
            _fig("运行次数", f"{t.runs:,} 次"),
            _fig("累计运行", duration(t.run_s), f"含估算的 {duration(t.estimated_s)}" if t.estimated_s else ""),
            _fig("其中挂机", duration(t.idle_s), f"占 {_rate(t.idle_s, t.run_s, 0)}" if t.run_s else ""),
            _fig("最长一次连续运行", duration(t.longest_run_s)),
            _fig("挂机时等 LB 恢复", duration(t.lb_wait_s)),
            _fig("用「跳过」消耗 LB", f"{t.skips:,} 次"),
        ]
        parts.append(f'<section class="card strip">{"".join(figs)}</section>')
        live_ids = {id(s) for s in view.live}
        runs = [*view.live, *sorted((s for s in view.sessions if id(s) not in live_ids), key=lambda s: -s.start)]
        longest = max((s.seconds for s in runs), default=0)
        rows = [_session_row(s, longest, id(s) in live_ids) for s in runs]
        parts.append(f'<section class="card tblcard mt pgc"><div class="tbar"><h3 class="h" style="margin:0">{_ic("u-history")}运行记录'
                     f'<span class="sub">每次打歌任务一行，新的在前</span></h3></div><div style="overflow-x:auto"><table><thead><tr><th>时间</th><th>任务</th><th>时长</th>'
                     f'<th class="num">局数</th><th class="num">AP</th><th class="num">等 LB</th><th class="num">跳过</th></tr></thead>'
                     f'<tbody data-pg="sp">{"".join(rows)}</tbody></table></div>{_pager("sp", len(rows), "次")}</section>')
    elif t.plays:
        parts.append(f'<section class="card strip" style="--n:4">{_fig("累计运行", duration(t.run_s), "按演奏记录估算")}'
                     f'{_fig("最长一次连续运行", duration(t.longest_run_s), "按演奏记录估算")}'
                     f'{_fig("开始使用", _d(t.first))}{_fig("演奏天数", f"{len(t.per_day):,} 天")}</section>'
                     f'<section class="card mt"><div class="note">{_ic("u-history")}<div>还没有运行记录。从这一版起，每次打歌任务的开始和结束时间、局数、AP、'
                     f'挂机时等 LB 的时间都会记在这里；之前的 {_e(duration(t.estimated_s or t.run_s))} 按演奏记录估算（相邻两局隔不到 10 分钟算一直在运行）。</div></div></section>')
    if view.plays:
        plays = view.plays[:PLAYS_MAX]
        cut = f"，只列出最近的 {len(plays):,} 局" if len(view.plays) > len(plays) else ""
        parts.append(f'<section class="card tblcard mt pgc"><div class="tbar"><h3 class="h" style="margin:0">{_ic("u-music")}全部演奏'
                     f'<span class="sub">每局一行，新的在前{cut}</span></h3></div><div style="overflow-x:auto"><table id="plays"><thead><tr><th>时间</th><th>曲目</th>'
                     f'<th>难度</th><th>判定</th><th>结果</th><th class="num">分数</th></tr></thead><tbody></tbody></table></div>{_pager("pp", len(plays), "局")}'
                     f'<script type="application/json" id="plays-data">{_plays_json(view, plays)}</script></section>')
    if not parts:
        return f'<section class="card empty">{ART}<div><h2>还没有运行记录</h2><p>每次打歌任务（重复刷歌、挂机、AP补完……）的时长、局数、AP、挂机时等 LB 的时间会记在这里。</p></div></section>'
    return "".join(parts)


# ---------------------------------------------------------------- 页面


def _header(view: View) -> str:
    t = view.totals
    got = sum(a.id in view.unlocked for a in ACHIEVEMENTS)
    since = f" · {_d(t.first)} 起" if t.first is not None else ""
    songs = len({mid for mid, _ in t.charts})
    counts = {"ach": f"{got}/{len(ACHIEVEMENTS)}", "charts": f"{songs:,}" if songs else ""}
    btns = []
    for k, name, icon in TABS:
        extra = f'<span class="cnt">{counts[k]}</span>' if counts.get(k) else ""
        if k == "runs" and view.live:
            extra = '<span class="dot" title="有打歌任务正在进行"></span>'
        btns.append(f'<button data-t="{k}" role="tab">{_ic(icon)}{name}{extra}</button>')
    return (f'<header class="top"><div class="wrap topin"><div class="brand"><span class="logo">{_ic("u-trophy")}</span><div>'
            f'<div class="ttl">统计与成就</div><div class="meta">Our Notes Auto · 更新于 {_e(_t(view.now))}{_e(since)} · 只统计本工具打的局</div></div></div>'
            f'<nav class="tabs" role="tablist">{"".join(btns)}</nav>'
            f'<button class="tool" type="button" data-bk-open aria-haspopup="dialog">{_ic("u-archive")}备份与恢复</button></div></header>')


def _backup_dialog(view: View) -> str:
    """「备份与恢复」对话框（按钮做什么见 BACKUP_JS）。没有记录时导出按钮不能点。"""
    t = view.totals
    got = sum(a.id in view.unlocked for a in ACHIEVEMENTS)
    has = bool(t.plays or t.runs or view.unlocked)
    chips = "".join(f"<span>{s}</span>" for s in (f"{t.plays:,} 局演奏", f"{t.runs:,} 次运行", f"{got} 个成就")) if has else "<span>还没有记录</span>"
    return ('<dialog class="bk" aria-labelledby="bk-t"><div class="bk-in">'
            f'<div class="bk-hd"><span class="logo">{_ic("u-archive")}</span><div><h2 id="bk-t">备份与恢复</h2>'
            '<p>演奏记录、运行记录、成就和学到的 offset 存成一个文件；换了新的包以后导入就能接着统计</p></div>'
            f'<button class="bk-x" type="button" data-bk-close title="关闭" aria-label="关闭">{_ic("u-x")}</button></div>'
            f'<div class="bk-off" hidden>{_ic("u-alert")}<div class="bk-offt"></div></div>'
            '<div class="bk-cards">'
            f'<section class="bk-card"><div class="bk-ct">{_ic("u-download")}导出</div>'
            '<p>选好存到哪里（放在包目录外面，如「文档」，删掉旧的包也还在），换包后在新的包里导入</p>'
            f'<div class="bk-sum">{chips}</div><div><button class="btn pri" type="button" data-bk="export"{"" if has else " data-empty"}>'
            f'{_ic("u-download")}导出备份…</button></div></section>'
            f'<section class="bk-card"><div class="bk-ct">{_ic("u-upload")}导入</div>'
            '<p>选以前导出的备份文件，先看会导入多少再确认。合并进来，这边已有的记录不变</p>'
            f'<div><button class="btn" type="button" data-bk="pick">{_ic("u-file")}选择备份文件…</button>'
            '<input type="file" accept=".zip,application/zip" hidden data-bk="file"></div></section></div>'
            f'<details class="bk-dir"><summary>{_ic("u-right")}没导出过？从旧的包目录导入</summary>'
            '<div class="bk-row"><input type="text" data-bk="dir" placeholder="旧的包目录的完整路径，如 D:\\OurNotes-Auto-v0.1.5-win-x64-MXU" '
            f'spellcheck="false" autocomplete="off" aria-label="旧的包目录"><button class="btn" type="button" data-bk="dirgo">{_ic("u-folder")}读取</button></div>'
            '<small>旧的包还在时，直接读它（或它的 data 文件夹）里的记录</small></details>'
            '<div class="bk-pv" hidden></div><div class="bk-msg" hidden role="status"></div>'
            '<p class="bk-foot">招募截图不在备份里。导入前的记录会先复制到 data\\import_backup（留最近 5 次）。</p>'
            '</div></dialog>')


def render_html(view: View) -> str:
    """「统计与成就」窗口的页面（data/stats.html）。"""
    panes = (("ov", _overview), ("ach", _achievements), ("data", _play_data), ("charts", _charts_tab), ("runs", _runs))
    body = "".join(f'<section class="pane" data-pane="{k}">{fn(view)}</section>' for k, fn in panes)
    return (f'<!doctype html><html lang="zh-CN" data-tab="ov"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(TITLE)}</title>'
            f'{_head_js()}<style>{CSS}{_tab_css()}</style></head><body>{SPRITE}{_header(view)}'
            f'<main class="wrap">{body}</main>{_backup_dialog(view)}<script>{PAGE_JS}</script>'
            f'<script>const BK_MAX={MAX_UPLOAD};{BACKUP_JS}</script>{live_block(view)}</body></html>')

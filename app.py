import os, json, time
from datetime import datetime, timezone

import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ---------------- Config ----------------
OPS_MONITOR_BASE_URL = os.getenv("OPS_MONITOR_BASE_URL", "http://127.0.0.1:4000").strip().rstrip("/")
OPS_MONITOR_TOKEN = os.getenv("OPS_MONITOR_TOKEN", "").strip()

DO_TOKEN = os.getenv("DO_TOKEN", "").strip()
RANGE_HOURS = int((os.getenv("RANGE_HOURS", "6").strip() or "6"))

SERVICES_JSON_RAW = os.getenv("SERVICES_JSON", "[]").strip()
try:
    SERVICES = json.loads(SERVICES_JSON_RAW) if SERVICES_JSON_RAW else []
except Exception:
    SERVICES = []

st.set_page_config(
    page_title="Deliberatorium Monitoring",
    page_icon="📡",
    layout="wide",
)

# ---------------- Theme / CSS ----------------
st.markdown(
    """
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }

/* cards */
.card {
  border: 1px solid rgba(255,255,255,.10);
  border-radius: 16px;
  padding: 14px 16px;
  background: rgba(255,255,255,.03);
  box-shadow: 0 8px 22px rgba(0,0,0,.20);
}

/* badges */
.badge { display:inline-block; padding: 2px 10px; border-radius: 999px; font-weight: 800; font-size: 12px; letter-spacing: .3px; }
.badge-up { background: rgba(46, 204, 113, .15); color: #2ecc71; border: 1px solid rgba(46, 204, 113, .35); }
.badge-down { background: rgba(231, 76, 60, .15); color: #e74c3c; border: 1px solid rgba(231, 76, 60, .35); }
.badge-unk { background: rgba(241, 196, 15, .15); color: #f1c40f; border: 1px solid rgba(241, 196, 15, .35); }

.small { font-size: 12px; opacity: .85; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12px; }

/* section titles */
.section-title { font-size: 18px; font-weight: 900; margin-bottom: 8px; }

/* banners */
.banner-down {
  border: 1px solid rgba(231, 76, 60, .35);
  background: rgba(231, 76, 60, .12);
  padding: 10px 12px;
  border-radius: 14px;
}
.banner-warn {
  border: 1px solid rgba(241, 196, 15, .35);
  background: rgba(241, 196, 15, .10);
  padding: 10px 12px;
  border-radius: 14px;
}
.banner-ok {
  border: 1px solid rgba(46, 204, 113, .35);
  background: rgba(46, 204, 113, .10);
  padding: 10px 12px;
  border-radius: 14px;
}

/* nicer links */
a { text-decoration: none !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------- Helpers ----------------
def _ops_headers():
    h = {"Accept": "application/json"}
    if OPS_MONITOR_TOKEN:
        h["x-dashboard-token"] = OPS_MONITOR_TOKEN
    return h

@st.cache_data(ttl=10)
def ops_get(path: str):
    url = f"{OPS_MONITOR_BASE_URL}{path}"
    r = requests.get(url, headers=_ops_headers(), timeout=8)
    r.raise_for_status()
    return r.json()

def _do_headers():
    if not DO_TOKEN:
        return None
    return {"Authorization": f"Bearer {DO_TOKEN}"}

def _parse_matrix(payload):
    try:
        result = payload.get("data", {}).get("result", [])
        if not result:
            return pd.DataFrame(columns=["ts", "value"])
        values = result[0].get("values", [])
        rows = []
        for ts, val in values:
            rows.append((datetime.fromtimestamp(int(ts), tz=timezone.utc), float(val)))
        return pd.DataFrame(rows, columns=["ts", "value"])
    except Exception:
        return pd.DataFrame(columns=["ts", "value"])

@st.cache_data(ttl=30)
def do_metric(metric: str, droplet_id: str, hours: int):
    if not DO_TOKEN or not droplet_id:
        return pd.DataFrame(columns=["ts", "value"])
    end = int(time.time())
    start = int(end - hours * 3600)
    url = f"https://api.digitalocean.com/v2/monitoring/metrics/droplet/{metric}?host_id={droplet_id}&start={start}&end={end}"
    r = requests.get(url, headers=_do_headers(), timeout=12)
    r.raise_for_status()
    return _parse_matrix(r.json())

def mem_used_pct(droplet_id: str, hours: int):
    avail = do_metric("memory_available", droplet_id, hours)
    total = do_metric("memory_total", droplet_id, hours)
    if avail.empty or total.empty:
        return pd.DataFrame(columns=["ts", "value"])
    df = pd.merge(avail, total, on="ts", suffixes=("_avail", "_total"))
    df["value"] = (1.0 - (df["value_avail"] / df["value_total"])) * 100.0
    return df[["ts", "value"]]

def badge(status: str):
    s = (status or "unknown").lower()
    if s == "up":
        return '<span class="badge badge-up">UP</span>'
    if s == "down":
        return '<span class="badge badge-down">DOWN</span>'
    return '<span class="badge badge-unk">UNKNOWN</span>'

def safe_link(url: str):
    if not url:
        return ""
    return f"[{url}]({url})"

# ---------------- Load summary ----------------
try:
    summary = ops_get("/dashboard/summary")
except Exception as e:
    st.error(f"Cannot reach monitoring agent: {e}")
    st.stop()

components = summary.get("components", {}) if isinstance(summary, dict) else {}

# overall status
statuses = []
for svc in SERVICES:
    key = svc.get("key")
    stt = (components.get(key, {}) or {}).get("status", "unknown")
    statuses.append(stt)

down_count = sum(1 for s in statuses if (s or "").lower() == "down")
up_count = sum(1 for s in statuses if (s or "").lower() == "up")
unk_count = len(statuses) - down_count - up_count

overall = "OK" if down_count == 0 and unk_count == 0 else ("DEGRADED" if down_count == 0 else "DOWN")
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# ---------------- Sidebar ----------------
with st.sidebar:
    st.markdown("### Controls")
    hours = st.slider("Time range (hours)", 1, 24, RANGE_HOURS, 1)
    show_only_errors = st.toggle("Logs: only error/fatal", value=False)
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# ---------------- Header ----------------
left, right = st.columns([3, 2])
with left:
    st.title("📡 Deliberatorium — Monitoring Dashboard")
    st.caption(f"Agent: {OPS_MONITOR_BASE_URL} • Last refresh: {now_iso}")

with right:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("**Overview**")
    st.markdown(f"Services: **{len(SERVICES)}**  •  Up: **{up_count}**  •  Down: **{down_count}**  •  Unknown: **{unk_count}**")
    st.markdown("</div>", unsafe_allow_html=True)

if overall == "DOWN":
    st.markdown('<div class="banner-down">🚨 Incident: one or more services are DOWN — check cards & logs.</div>', unsafe_allow_html=True)
elif overall == "DEGRADED":
    st.markdown('<div class="banner-warn">⚠️ Some services are UNKNOWN (checks not running yet or missing config).</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="banner-ok">✅ All monitored services are UP.</div>', unsafe_allow_html=True)

st.divider()

tab_overview, tab_metrics, tab_logs = st.tabs(["🏠 Overview", "📈 Metrics", "🧾 Logs & Alerts"])

# ---------------- Overview ----------------
with tab_overview:
    st.markdown('<div class="section-title">Service Status</div>', unsafe_allow_html=True)

    if not SERVICES:
        st.warning("SERVICES_JSON is empty. Add Frontend/Backend/Dev in .env")
    else:
        cols = st.columns(len(SERVICES))
        for i, svc in enumerate(SERVICES):
            key = svc.get("key")
            label = svc.get("label", key)
            url = svc.get("url", "")
            droplet_id = svc.get("droplet_id", "")

            comp = components.get(key, {}) or {}
            stt = comp.get("status", "unknown")
            last_check = comp.get("lastCheckAt")
            last_ok = comp.get("lastOkAt")
            last_err = comp.get("lastError")

            with cols[i]:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown(f"**{label}**")
                st.markdown(f"Status: {badge(stt)}", unsafe_allow_html=True)
                if url:
                    st.markdown(f"URL: {safe_link(url)}")
                st.markdown(f"<div class='small'>Droplet: <span class='mono'>{droplet_id}</span></div>", unsafe_allow_html=True)
                if last_check:
                    st.markdown(f"<div class='small'>Last check: <span class='mono'>{last_check}</span></div>", unsafe_allow_html=True)
                if last_ok:
                    st.markdown(f"<div class='small'>Last OK: <span class='mono'>{last_ok}</span></div>", unsafe_allow_html=True)
                if last_err:
                    st.markdown(f"<div class='small'>Last error: <span class='mono'>{last_err}</span></div>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    st.markdown('<div class="section-title">Quick Metrics</div>', unsafe_allow_html=True)
    st.caption("CPU is converted to % if it looks fractional. Memory Used is computed from total/available.")

    if not DO_TOKEN:
        st.warning("DO_TOKEN missing → metrics disabled.")
    else:
        for svc in SERVICES[:2]:
            label = svc.get("label", svc.get("key"))
            droplet_id = svc.get("droplet_id", "")
            if not droplet_id:
                continue

            st.markdown(f"#### {label}")
            c1, c2 = st.columns(2)

            with c1:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown("**CPU**")
                cpu = do_metric("cpu", droplet_id, hours)
                if cpu.empty:
                    st.info("No CPU data yet (do-agent may need a few minutes, or monitoring not enabled).")
                else:
                    cpu_plot = cpu.copy()
                    if cpu_plot["value"].max() <= 1.5:
                        cpu_plot["value"] = cpu_plot["value"] * 100.0
                    st.line_chart(cpu_plot.set_index("ts")["value"])
                st.markdown("</div>", unsafe_allow_html=True)

            with c2:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown("**Memory Used (%)**")
                mem = mem_used_pct(droplet_id, hours)
                if mem.empty:
                    st.info("No memory data yet (do-agent may need a few minutes).")
                else:
                    st.line_chart(mem.set_index("ts")["value"])
                st.markdown("</div>", unsafe_allow_html=True)

# ---------------- Metrics explorer ----------------
with tab_metrics:
    st.markdown('<div class="section-title">Metrics Explorer</div>', unsafe_allow_html=True)
    if not DO_TOKEN:
        st.warning("DO_TOKEN missing → metrics disabled.")
    else:
        svc_labels = [s.get("label", s.get("key")) for s in SERVICES]
        label_to_svc = {s.get("label", s.get("key")): s for s in SERVICES}
        pick = st.selectbox("Select a service", svc_labels, index=0 if svc_labels else None)
        svc = label_to_svc.get(pick)
        if svc:
            droplet_id = svc.get("droplet_id", "")
            st.markdown(f"**Droplet:** `{droplet_id}`")
            c1, c2 = st.columns(2)

            with c1:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown("**CPU**")
                cpu = do_metric("cpu", droplet_id, hours)
                if cpu.empty:
                    st.info("No CPU data yet (check do-agent).")
                else:
                    cpu_plot = cpu.copy()
                    if cpu_plot["value"].max() <= 1.5:
                        cpu_plot["value"] = cpu_plot["value"] * 100.0
                    st.line_chart(cpu_plot.set_index("ts")["value"])
                st.markdown("</div>", unsafe_allow_html=True)

            with c2:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown("**Memory Used (%)**")
                mem = mem_used_pct(droplet_id, hours)
                if mem.empty:
                    st.info("No memory data yet (check do-agent).")
                else:
                    st.line_chart(mem.set_index("ts")["value"])
                st.markdown("</div>", unsafe_allow_html=True)

# ---------------- Logs ----------------
with tab_logs:
    st.markdown('<div class="section-title">Logs & Alerts</div>', unsafe_allow_html=True)

    try:
        logs_payload = ops_get("/dashboard/logs?limit=300")
        logs = logs_payload.get("logs", []) or []
    except Exception:
        logs = []

    if not logs:
        st.info("No logs yet. Send logs to /logs to populate.")
    else:
        df = pd.DataFrame(logs)
        if "ts" in df.columns:
            df = df.sort_values("ts", ascending=False)
        if show_only_errors and "level" in df.columns:
            df = df[df["level"].isin(["error", "fatal"])]

        st.dataframe(df, use_container_width=True, height=520)

    st.caption(
        "Test log: curl -s http://127.0.0.1:4000/logs -H 'Content-Type: application/json' "
        "-d '{\"source\":\"test\",\"level\":\"info\",\"message\":\"hello\"}'"
    )

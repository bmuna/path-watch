import { useState, useEffect, useRef, useCallback } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts'
import './App.css'

const WS_URL = 'ws://localhost:8000/ws/live'
const API    = 'http://localhost:8000/api'

// ── WebSocket hook ────────────────────────────────────────────────────────────
function useLive() {
  const [data, setData] = useState(null)
  const ws = useRef(null)
  useEffect(() => {
    function connect() {
      ws.current = new WebSocket(WS_URL)
      ws.current.onmessage = e => setData(JSON.parse(e.data))
      ws.current.onclose = () => setTimeout(connect, 2000)
      ws.current.onerror = () => ws.current?.close()
    }
    connect()
    return () => ws.current?.close()
  }, [])
  return data
}

// ── helpers ───────────────────────────────────────────────────────────────────
function scoreColor(score) {
  if (score >= 70) return '#ff6b6b'
  if (score >= 40) return '#fcc419'
  if (score >= 15) return '#f0a05a'
  return '#51cf66'
}

function scoreLabel(score) {
  if (score >= 70) return 'Throttled'
  if (score >= 40) return 'Degraded'
  if (score >= 15) return 'Slow'
  return 'Normal'
}

// ── components ────────────────────────────────────────────────────────────────
function Kpi({ label, value, color, sub }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={color ? { color } : {}}>{value || '—'}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

function Alert({ a }) {
  const colors = { danger: '#ff6b6b', warning: '#fcc419', info: '#4dabf7', success: '#51cf66' }
  const bg     = { danger: '#2a1215', warning: '#2a2412', info: '#12202a', success: '#122a15' }
  return (
    <div className="alert" style={{ borderLeft: `3px solid ${colors[a.level]}`, background: bg[a.level] }}>
      <div className="alert-title" style={{ color: colors[a.level] }}>{a.title}</div>
      <div className="alert-detail">{a.detail}</div>
    </div>
  )
}

function ThrottleGauge({ score }) {
  const color = scoreColor(score)
  const label = scoreLabel(score)
  const dash  = 2 * Math.PI * 40
  const fill  = dash * (score / 100)
  return (
    <div className="gauge-wrap">
      <svg width={110} height={110} viewBox="0 0 110 110">
        <circle cx={55} cy={55} r={40} fill="none" stroke="#2f3a46" strokeWidth={10} />
        <circle cx={55} cy={55} r={40} fill="none" stroke={color} strokeWidth={10}
                strokeDasharray={`${fill} ${dash}`}
                strokeLinecap="round"
                transform="rotate(-90 55 55)" />
        <text x={55} y={52} textAnchor="middle" fill={color} fontSize={24} fontWeight={700}>{score}</text>
        <text x={55} y={68} textAnchor="middle" fill="#9eafc0" fontSize={11}>{label}</text>
      </svg>
      <div className="gauge-label">Throttle Score</div>
    </div>
  )
}

function HeatmapGrid({ data }) {
  if (!data?.length) return <div className="empty">Heatmap appears after logging across multiple hours and days.</div>
  const hours = [...new Set(data.map(d => d.hour))].sort((a, b) => a - b)
  const DAY_ORDER = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const dayMap = { Monday:'Mon', Tuesday:'Tue', Wednesday:'Wed', Thursday:'Thu', Friday:'Fri', Saturday:'Sat', Sunday:'Sun' }
  const activeDays = DAY_ORDER.filter(s => data.some(d => dayMap[d.day] === s || d.day === s))
  const maxVal = Math.max(...data.map(d => d.value), 0.1)
  const lookup = {}
  data.forEach(d => { lookup[`${d.hour}-${dayMap[d.day] || d.day}`] = d.value })
  return (
    <div className="heatmap">
      <div className="heatmap-row">
        <div className="hm-hour"></div>
        {activeDays.map(d => <div key={d} className="hm-cell hm-head">{d}</div>)}
      </div>
      {hours.map(h => (
        <div key={h} className="heatmap-row">
          <div className="hm-hour">{String(h).padStart(2,'0')}:00</div>
          {activeDays.map(d => {
            const v = lookup[`${h}-${d}`]
            const alpha = v != null ? 0.15 + (v / maxVal) * 0.75 : 0
            const isSlow = v != null && v < maxVal * 0.4
            return (
              <div key={d} className="hm-cell"
                   style={{ background: v != null ? (isSlow ? `rgba(255,107,107,${alpha})` : `rgba(77,171,247,${alpha})`) : 'rgba(255,255,255,0.02)' }}
                   title={v != null ? `${v.toFixed(2)} Mbps` : ''}>
                {v != null ? v.toFixed(1) : ''}
              </div>
            )
          })}
        </div>
      ))}
      <div className="hm-legend">
        <span style={{color:'#4dabf7'}}>■</span> Fast &nbsp;
        <span style={{color:'#ff6b6b'}}>■</span> Slow
      </div>
    </div>
  )
}

function MapView({ liveGeo }) {
  // group points by unique lat/lon
  const points = Object.values(
    (liveGeo || []).reduce((acc, g) => {
      if (!g.lat || !g.lon) return acc
      const key = `${g.lat},${g.lon}`
      if (!acc[key]) acc[key] = { ...g, count: 0, ips: [] }
      acc[key].count++
      acc[key].ips.push(g.ip)
      return acc
    }, {})
  )

  return (
    <MapContainer center={[20, 0]} zoom={2} className="map" attributionControl={false}>
      <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
      {points.map((p, i) => (
        <CircleMarker key={i}
          center={[p.lat, p.lon]}
          radius={Math.min(4 + p.count * 1.5, 18)}
          pathOptions={{ color: '#4dabf7', fillColor: '#4dabf7', fillOpacity: 0.7, weight: 1 }}
        >
          <Popup>
            <div style={{ color: '#000', minWidth: 140 }}>
              <strong>{p.city || p.country || p.ip}</strong><br />
              {p.country && <>{p.country}<br /></>}
              <span style={{ color: '#666', fontSize: 11 }}>{p.isp || p.org || ''}</span><br />
              <span style={{ color: '#666', fontSize: 11 }}>IPs: {p.ips.slice(0, 3).join(', ')}</span><br />
              <span style={{ color: '#666', fontSize: 11 }}>{p.count} active connection{p.count !== 1 ? 's' : ''}</span>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}

function AppList({ apps }) {
  if (!apps?.length) return <div className="empty">Open apps — they appear here once they use the network.</div>
  return (
    <div className="app-list">
      {apps.map((a, i) => (
        <div key={a.name + i} className="app-row">
          <div className="app-icon">{a.name.charAt(0).toUpperCase()}</div>
          <div className="app-info">
            <div className="app-name">{a.name}</div>
            <div className="app-sub">{a.conns} socket{a.conns !== 1 ? 's' : ''}{a.top_hosts?.[0] ? ` · ${a.top_hosts[0]}` : ''}</div>
          </div>
          <div className="app-size">{a.total_fmt}</div>
        </div>
      ))}
    </div>
  )
}

// ── main app ──────────────────────────────────────────────────────────────────
export default function App() {
  const live = useLive()
  const [analysis, setAnalysis] = useState(null)
  const [speedHist, setSpeedHist] = useState([])
  const [scoreHist, setScoreHist] = useState([])
  const histRef  = useRef([])
  const scoreRef = useRef([])
  const [tab, setTab] = useState('overview')

  const fetchAnalysis = useCallback(() => {
    fetch(`${API}/analysis`).then(r => r.json()).then(setAnalysis).catch(() => {})
  }, [])

  useEffect(() => { fetchAnalysis(); const t = setInterval(fetchAnalysis, 15000); return () => clearInterval(t) }, [fetchAnalysis])

  useEffect(() => {
    if (!live) return
    const n = histRef.current.length
    histRef.current  = [...histRef.current.slice(-89),  { t: n, down: live.down_mbps, up: live.up_mbps }]
    scoreRef.current = [...scoreRef.current.slice(-59), { t: n, score: live.throttle_score || 0 }]
    setSpeedHist([...histRef.current])
    setScoreHist([...scoreRef.current])
  }, [live])

  const vpnOn = live?.vpn === 'vpn'
  const score = live?.throttle_score || 0

  const TABS = ['overview', 'map', 'heatmap', 'throttling', 'apps']

  return (
    <div className="app">
      {/* header */}
      <header className="header">
        <div className="brand">Path Watch</div>
        <div className="header-sub">Live ISP intelligence · passive · no probing</div>
        <div className="header-right">
          <div className="score-pill" style={{ background: scoreColor(score) + '22', border: `1px solid ${scoreColor(score)}`, color: scoreColor(score) }}>
            {scoreLabel(score)} · {score}
          </div>
          <span className="clock">{live?.ts ? new Date(live.ts).toLocaleTimeString() : '—'}</span>
          <span className={`dot ${live ? 'live' : ''}`} />
        </div>
      </header>

      {/* KPI row */}
      <div className="kpi-row">
        <Kpi label="Download"   value={live ? `${live.down_mbps.toFixed(2)} Mbps` : '—'} />
        <Kpi label="Upload"     value={live ? `${live.up_mbps.toFixed(2)} Mbps` : '—'} />
        <Kpi label="VPN"        value={vpnOn ? 'ON' : 'OFF'} color={vpnOn ? '#51cf66' : '#ff6b6b'} />
        <Kpi label="Connection" value={live?.connection?.toUpperCase() || '—'} sub={live?.ssid || ''} />
        <Kpi label="Time"       value={live?.tod || '—'} />
        <Kpi label="Your IP"    value={live?.public_ip || '—'} sub={live?.ip_country || ''} />
        <Kpi label="Sockets"    value={live?.active_count?.toString() || '—'} />
        <Kpi label="Session"    value={live?.session_total_fmt || '0 B'} />
      </div>

      {/* tabs */}
      <div className="tabs">
        {TABS.map(t => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* ── overview ── */}
      {tab === 'overview' && (
        <div className="content">
          <div className="grid-2">
            <div className="panel">
              <h3>Live Speed</h3>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={speedHist}>
                  <defs>
                    <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#4dabf7" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#4dabf7" stopOpacity={0}    />
                    </linearGradient>
                    <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor="#fcc419" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#fcc419" stopOpacity={0}    />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2832" />
                  <XAxis dataKey="t" hide />
                  <YAxis stroke="#6b7b8d" tick={{ fontSize: 11 }} width={38} tickFormatter={v => `${v}`} />
                  <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }}
                           labelStyle={{ display: 'none' }}
                           formatter={(v, k) => [`${v.toFixed(2)} Mbps`, k === 'down' ? '↓ Download' : '↑ Upload']} />
                  <Area type="monotone" dataKey="down" stroke="#4dabf7" fill="url(#gDown)" strokeWidth={2} dot={false} />
                  <Area type="monotone" dataKey="up"   stroke="#fcc419" fill="url(#gUp)"   strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
              <div className="chart-legend">
                <span><span className="leg" style={{ background: '#4dabf7' }} /> Download</span>
                <span><span className="leg" style={{ background: '#fcc419' }} /> Upload</span>
              </div>
            </div>
            <div className="panel gauge-panel">
              <h3>Throttle Score</h3>
              <div className="gauge-row">
                <ThrottleGauge score={score} />
                <div className="score-detail">
                  <div className="score-reason">{live?.throttle_reason || 'Calculating…'}</div>
                  {live?.baseline_down != null && (
                    <div className="score-baseline">Baseline: {live.baseline_down} Mbps</div>
                  )}
                  <div className="score-hist-label">60-sample trend</div>
                  <ResponsiveContainer width="100%" height={80}>
                    <AreaChart data={scoreHist}>
                      <defs>
                        <linearGradient id="gScore" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%"  stopColor="#ff6b6b" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="#ff6b6b" stopOpacity={0}   />
                        </linearGradient>
                      </defs>
                      <YAxis domain={[0, 100]} hide />
                      <Area type="monotone" dataKey="score" stroke="#ff6b6b" fill="url(#gScore)" strokeWidth={2} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </div>

          <div className="grid-2">
            <div className="panel">
              <h3>Alerts</h3>
              {analysis?.alerts?.map((a, i) => <Alert key={i} a={a} />) || <div className="empty">Analyzing…</div>}
              <button className="btn" onClick={fetchAnalysis}>Refresh</button>
            </div>
            <div className="panel">
              <h3>Conditions Logged</h3>
              {analysis?.conditions?.length ? (
                <div className="conditions">
                  {analysis.conditions.map((c, i) => (
                    <div key={i} className="cond-card">
                      <div className="cond-label">{c.label}</div>
                      <div className="cond-val">↓ {c.down_median} Mbps</div>
                      <div className="cond-n">n={c.n}</div>
                    </div>
                  ))}
                </div>
              ) : <div className="empty">Switch VPN / hotspot / times to build conditions.</div>}
            </div>
          </div>
        </div>
      )}

      {/* ── map ── */}
      {tab === 'map' && (
        <div className="content">
          <div className="panel" style={{ padding: 0, overflow: 'hidden', borderRadius: 12 }}>
            <div style={{ padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 }}>
              <h3 style={{ margin: 0 }}>Live Connections — World Map</h3>
              <span className="muted" style={{ fontSize: 13 }}>
                {live?.live_geo?.length || 0} geo-located · {live?.active_count || 0} total sockets
              </span>
            </div>
            <MapView liveGeo={live?.live_geo} />
          </div>
          <div className="panel" style={{ marginTop: 14 }}>
            <h3>ISP / Network Analysis</h3>
            {analysis?.isp_analysis?.length ? (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={analysis.isp_analysis.slice(0, 10)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2832" />
                  <XAxis type="number" stroke="#6b7b8d" tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="geo_isp" stroke="#6b7b8d" tick={{ fontSize: 11 }} width={160} />
                  <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }} />
                  <Bar dataKey="connections" fill="#4dabf7" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="empty">Geo data resolves after a few seconds of connections. Stay on this tab.</div>}
          </div>
        </div>
      )}

      {/* ── heatmap ── */}
      {tab === 'heatmap' && (
        <div className="content">
          <div className="panel">
            <h3>Download Speed — Hour × Day Heatmap</h3>
            <p className="muted" style={{ fontSize: 12, marginBottom: 14 }}>
              Red = slow, blue = fast. Shows when throttling is most likely to occur.
            </p>
            <HeatmapGrid data={analysis?.heatmap} />
          </div>
          <div className="grid-2" style={{ marginTop: 14 }}>
            <div className="panel">
              <h3>By Time of Day</h3>
              {analysis?.time_comparison?.length ? (
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={analysis.time_comparison}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e2832" />
                    <XAxis dataKey="tod" stroke="#6b7b8d" tick={{ fontSize: 12 }} />
                    <YAxis stroke="#6b7b8d" tick={{ fontSize: 11 }} width={40} />
                    <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }}
                             formatter={v => [`${v.toFixed(2)} Mbps`]} />
                    <Bar dataKey="median" radius={[4, 4, 0, 0]}>
                      {analysis.time_comparison.map((e, i) => {
                        const min = Math.min(...analysis.time_comparison.map(t => t.median))
                        return <Cell key={i} fill={e.median === min ? '#ff6b6b' : '#4dabf7'} />
                      })}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : <div className="empty">Log across morning / afternoon / evening / night.</div>}
            </div>
            <div className="panel">
              <h3>WiFi vs Hotspot</h3>
              {analysis?.connection_comparison && Object.keys(analysis.connection_comparison).length > 1 ? (
                <div className="conn-compare">
                  {Object.entries(analysis.connection_comparison).map(([k, v]) => (
                    <div key={k} className="conn-row">
                      <div className="conn-label">{k.toUpperCase()}</div>
                      <div className="conn-bar-wrap">
                        <div className="conn-bar" style={{
                          width: `${Math.min(100, v.median / Math.max(...Object.values(analysis.connection_comparison).map(x => x.median)) * 100)}%`,
                          background: k === 'wifi' ? '#4dabf7' : '#fcc419',
                        }} />
                      </div>
                      <div className="conn-val">{v.median} Mbps (n={v.n})</div>
                    </div>
                  ))}
                </div>
              ) : <div className="empty">Use both WiFi and hotspot to unlock this comparison.</div>}
            </div>
          </div>
        </div>
      )}

      {/* ── throttling ── */}
      {tab === 'throttling' && (
        <div className="content">
          <div className="grid-2">
            <div className="panel">
              <h3>VPN Comparison</h3>
              {analysis?.vpn_comparison ? (
                <>
                  <div className="vpn-bars">
                    {[['VPN OFF', analysis.vpn_comparison.vpn_off_median, '#ff6b6b'],
                      ['VPN ON',  analysis.vpn_comparison.vpn_on_median,  '#51cf66']].map(([lbl, val, col]) => (
                      <div key={lbl} className="vpn-bar-row">
                        <div className="vpn-bar-lbl">{lbl}</div>
                        <div className="vpn-bar-track">
                          <div className="vpn-bar-fill" style={{
                            width: `${Math.min(100, val / Math.max(analysis.vpn_comparison.vpn_on_median, analysis.vpn_comparison.vpn_off_median, 0.01) * 100)}%`,
                            background: col,
                          }} />
                        </div>
                        <div className="vpn-bar-val">{val.toFixed(2)} Mbps</div>
                      </div>
                    ))}
                  </div>
                  <div className="vpn-stat">
                    p = {analysis.vpn_comparison.p_value}
                    &nbsp;·&nbsp;
                    {analysis.vpn_comparison.significant ? '✓ Statistically significant' : '✗ Not significant'}
                    <br />n: {analysis.vpn_comparison.vpn_on_n} (on) / {analysis.vpn_comparison.vpn_off_n} (off)
                  </div>
                </>
              ) : <div className="empty">Turn VPN on and off while the app runs. The comparison appears here.</div>}
            </div>
            <div className="panel">
              <h3>Speed Trend This Session</h3>
              {analysis?.session_trend?.length ? (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={analysis.session_trend}>
                    <defs>
                      <linearGradient id="gTrend" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor="#4dabf7" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="#4dabf7" stopOpacity={0}    />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e2832" />
                    <XAxis dataKey="seq" hide />
                    <YAxis stroke="#6b7b8d" tick={{ fontSize: 11 }} width={38} />
                    <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }}
                             formatter={v => [`${v.toFixed(2)} Mbps`]} />
                    <Area type="monotone" dataKey="down" stroke="#4dabf7" fill="url(#gTrend)" strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : <div className="empty">Speed trend appears after 20+ samples.</div>}
            </div>
          </div>
          <div className="panel" style={{ marginTop: 14 }}>
            <h3>All Alerts</h3>
            {analysis?.alerts?.map((a, i) => <Alert key={i} a={a} />) || <div className="empty">Analyzing…</div>}
          </div>
        </div>
      )}

      {/* ── apps ── */}
      {tab === 'apps' && (
        <div className="content">
          <div className="panel">
            <h3>Apps Using Your Network</h3>
            <p className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
              One row per app. Hosts, socket count, and session usage. No payload inspection.
            </p>
            <AppList apps={live?.apps} />
          </div>
        </div>
      )}

      <footer className="footer">
        Passive · metadata only · no site probing · {live?.label || '—'} · vantage {live?.public_ip || '—'}
      </footer>
    </div>
  )
}

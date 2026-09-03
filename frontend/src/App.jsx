import { useState, useEffect, useRef, useCallback } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts'
import './App.css'

const WS  = 'ws://localhost:8000/ws/live'
const API = 'http://localhost:8000/api'

function useLive() {
  const [d, setD] = useState(null)
  const ws = useRef(null)
  useEffect(() => {
    function open() {
      ws.current = new WebSocket(WS)
      ws.current.onmessage = e => setD(JSON.parse(e.data))
      ws.current.onclose   = () => setTimeout(open, 2000)
      ws.current.onerror   = () => ws.current?.close()
    }
    open()
    return () => ws.current?.close()
  }, [])
  return d
}

function scoreColor(s) {
  if (s >= 70) return 'var(--red)'
  if (s >= 40) return 'var(--amber)'
  if (s >= 15) return 'var(--amber)'
  return 'var(--green)'
}
function scoreWord(s) {
  if (s >= 70) return 'Throttled'
  if (s >= 40) return 'Degraded'
  if (s >= 15) return 'Slow'
  return 'Normal'
}

// ─── Stat ────────────────────────────────────────────────────────────────────
function Stat({ label, value, accent }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value" style={accent ? { color: accent } : {}}>{value ?? '—'}</span>
    </div>
  )
}

// ─── Section header ───────────────────────────────────────────────────────────
function Section({ title, right }) {
  return (
    <div className="section-head">
      <span className="section-title">{title}</span>
      {right && <span className="section-right">{right}</span>}
    </div>
  )
}

// ─── Alert row ───────────────────────────────────────────────────────────────
const LEVEL_COLOR = {
  danger:  'var(--red)',
  warning: 'var(--amber)',
  info:    'var(--blue)',
  success: 'var(--green)',
}
function AlertRow({ a }) {
  return (
    <div className="alert-row">
      <span className="alert-dot" style={{ background: LEVEL_COLOR[a.level] ?? 'var(--blue)' }} />
      <div>
        <div className="alert-title">{a.title}</div>
        <div className="alert-body">{a.detail}</div>
      </div>
    </div>
  )
}

// ─── Heatmap ─────────────────────────────────────────────────────────────────
function Heatmap({ data }) {
  if (!data?.length) return <p className="empty">Needs data across multiple hours and days.</p>
  const dayMap = { Monday:'Mon', Tuesday:'Tue', Wednesday:'Wed', Thursday:'Thu', Friday:'Fri', Saturday:'Sat', Sunday:'Sun' }
  const ORDER  = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
  const hours  = [...new Set(data.map(d => d.hour))].sort((a, b) => a - b)
  const days   = ORDER.filter(s => data.some(d => (dayMap[d.day] || d.day) === s))
  const lookup = {}
  data.forEach(d => { lookup[`${d.hour}-${dayMap[d.day] || d.day}`] = d.value })
  const maxV   = Math.max(...data.map(d => d.value), 0.01)

  return (
    <div className="hm-wrap">
      <div className="hm-row">
        <div className="hm-hour" />
        {days.map(d => <div key={d} className="hm-cell hm-day">{d}</div>)}
      </div>
      {hours.map(h => (
        <div key={h} className="hm-row">
          <div className="hm-hour">{String(h).padStart(2,'0')}:00</div>
          {days.map(d => {
            const v   = lookup[`${h}-${d}`]
            const frac = v != null ? v / maxV : 0
            const slow = v != null && frac < 0.45
            const bg   = v != null
              ? `rgba(${slow ? '255,107,107' : '74,158,247'},${0.08 + frac * 0.72})`
              : 'rgba(255,255,255,0.02)'
            return (
              <div key={d} className="hm-cell hm-data" style={{ background: bg }} title={v != null ? `${v.toFixed(2)} Mbps` : ''}>
                {v != null ? v.toFixed(1) : ''}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ─── App list ─────────────────────────────────────────────────────────────────
function AppList({ apps }) {
  if (!apps?.length) return <p className="empty">Open any app — connections appear here.</p>
  return (
    <table className="app-table">
      <thead>
        <tr>
          <th>App</th>
          <th>Sockets</th>
          <th>Top host</th>
          <th className="right">Session</th>
        </tr>
      </thead>
      <tbody>
        {apps.map((a, i) => (
          <tr key={a.name + i}>
            <td className="app-name-cell">{a.name}</td>
            <td className="mono">{a.conns}</td>
            <td className="muted truncate">{a.top_hosts?.[0] || '—'}</td>
            <td className="right mono">{a.total_fmt}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ─── Map ─────────────────────────────────────────────────────────────────────
function GeoMap({ points }) {
  const grouped = Object.values(
    (points || []).reduce((acc, g) => {
      if (!g?.lat || !g?.lon) return acc
      const k = `${g.lat},${g.lon}`
      if (!acc[k]) acc[k] = { ...g, count: 0 }
      acc[k].count++
      return acc
    }, {})
  )

  return (
    <MapContainer center={[20, 10]} zoom={2} className="map-canvas" attributionControl={false} zoomControl={false}>
      <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png" />
      {grouped.map((p, i) => (
        <CircleMarker key={i} center={[p.lat, p.lon]}
          radius={Math.min(3 + p.count * 1.2, 16)}
          pathOptions={{ color: '#4a9ef7', fillColor: '#4a9ef7', fillOpacity: 0.65, weight: 0 }}
        >
          <Popup>
            <div className="map-popup">
              <strong>{p.city || p.country || p.ip}</strong>
              {p.isp && <div>{p.isp}</div>}
              <div className="map-popup-sub">{p.count} connection{p.count !== 1 ? 's' : ''}</div>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}

// ─── Tooltip shared style ─────────────────────────────────────────────────────
const TT_STYLE = { background: '#161b22', border: '1px solid #2a3340', borderRadius: 6, fontSize: 12 }

// ─── Main ────────────────────────────────────────────────────────────────────
export default function App() {
  const live     = useLive()
  const [data, setData]   = useState(null)
  const [tab,  setTab]    = useState('monitor')
  const speedBuf = useRef([])
  const [speed, setSpeed] = useState([])

  const refresh = useCallback(() => {
    fetch(`${API}/analysis`).then(r => r.json()).then(setData).catch(() => {})
  }, [])

  useEffect(() => { refresh(); const t = setInterval(refresh, 15000); return () => clearInterval(t) }, [refresh])

  useEffect(() => {
    if (!live) return
    speedBuf.current = [...speedBuf.current.slice(-89), { down: live.down_mbps, up: live.up_mbps }]
    setSpeed([...speedBuf.current])
  }, [live])

  const vpn    = live?.vpn === 'vpn'
  const score  = live?.throttle_score ?? 0
  const sc     = scoreColor(score)
  const sw     = scoreWord(score)

  const TABS = ['monitor', 'map', 'analysis', 'apps']

  return (
    <div className="root">
      {/* ── nav ── */}
      <nav className="nav">
        <span className="nav-brand">Path Watch</span>
        <div className="nav-tabs">
          {TABS.map(t => (
            <button key={t} className={`nav-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </div>
        <div className="nav-right">
          <span className="status-badge" style={{ color: sc, borderColor: sc + '44', background: sc + '11' }}>
            {sw} · {score}
          </span>
          <span className={`led ${live ? 'on' : ''}`} />
        </div>
      </nav>

      <main className="main">

        {/* ── monitor ── */}
        {tab === 'monitor' && <>
          {/* stats row */}
          <div className="stats-row">
            <Stat label="Download"   value={live ? `${live.down_mbps.toFixed(2)} Mbps` : null} />
            <Stat label="Upload"     value={live ? `${live.up_mbps.toFixed(2)} Mbps` : null} />
            <Stat label="VPN"        value={vpn ? 'On' : 'Off'} accent={vpn ? 'var(--green)' : 'var(--text-2)'} />
            <Stat label="Link"       value={live?.connection ?? null} />
            <Stat label="Time"       value={live?.tod ?? null} />
            <Stat label="IP"         value={live?.public_ip ?? null} />
            <Stat label="Sockets"    value={live?.active_count?.toString() ?? null} />
          </div>

          {/* two columns */}
          <div className="cols">
            <div className="col col-wide">
              <div className="card">
                <Section title="Live speed" right={<span className="mono text-2">{live ? `↓ ${live.down_mbps.toFixed(2)}  ↑ ${live.up_mbps.toFixed(2)} Mbps` : ''}</span>} />
                <ResponsiveContainer width="100%" height={180}>
                  <AreaChart data={speed} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="gd" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%"   stopColor="#4a9ef7" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="#4a9ef7" stopOpacity={0}    />
                      </linearGradient>
                      <linearGradient id="gu" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%"   stopColor="#e8a838" stopOpacity={0.2} />
                        <stop offset="100%" stopColor="#e8a838" stopOpacity={0}   />
                      </linearGradient>
                    </defs>
                    <XAxis hide />
                    <YAxis stroke="transparent" tick={{ fill: 'var(--text-3)', fontSize: 10 }} width={30} />
                    <Tooltip contentStyle={TT_STYLE} labelStyle={{ display: 'none' }}
                      formatter={(v, k) => [`${v.toFixed(2)} Mbps`, k === 'down' ? '↓' : '↑']} />
                    <Area type="monotone" dataKey="down" stroke="#4a9ef7" fill="url(#gd)" strokeWidth={1.5} dot={false} />
                    <Area type="monotone" dataKey="up"   stroke="#e8a838" fill="url(#gu)" strokeWidth={1.5} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
                <div className="legend">
                  <span><i className="leg-dot" style={{ background: '#4a9ef7' }} />Download</span>
                  <span><i className="leg-dot" style={{ background: '#e8a838' }} />Upload</span>
                </div>
              </div>

              <div className="card" style={{ marginTop: 12 }}>
                <Section title="Conditions logged" />
                {data?.conditions?.length
                  ? <div className="cond-grid">
                      {data.conditions.map((c, i) => (
                        <div key={i} className="cond-item">
                          <code className="cond-code">{c.label}</code>
                          <span className="cond-val">{c.down_median} Mbps</span>
                          <span className="text-3">n={c.n}</span>
                        </div>
                      ))}
                    </div>
                  : <p className="empty">Switch VPN / hotspot / times to build contrast.</p>}
              </div>
            </div>

            <div className="col">
              <div className="card">
                <Section title="Throttle score" right={<span style={{ color: sc, fontWeight: 600 }}>{sw}</span>} />
                <div className="score-block">
                  <span className="score-num" style={{ color: sc }}>{score}</span>
                  <span className="score-bar-wrap">
                    <span className="score-bar-fill" style={{ width: `${score}%`, background: sc }} />
                  </span>
                </div>
                {live?.throttle_reason && <p className="score-reason">{live.throttle_reason}</p>}
                {live?.baseline_down != null && (
                  <p className="text-3" style={{ marginTop: 4 }}>Baseline {live.baseline_down} Mbps</p>
                )}
              </div>

              <div className="card" style={{ marginTop: 12, flex: 1 }}>
                <Section title="Findings" />
                {data?.alerts?.length
                  ? data.alerts.map((a, i) => <AlertRow key={i} a={a} />)
                  : <p className="empty">Analyzing…</p>}
                <button className="btn" onClick={refresh} style={{ marginTop: 12 }}>Refresh</button>
              </div>
            </div>
          </div>
        </>}

        {/* ── map ── */}
        {tab === 'map' && <>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="card-head-bar">
              <Section title="Live connections" right={`${live?.live_geo?.length ?? 0} geo-located of ${live?.active_count ?? 0} sockets`} />
            </div>
            <GeoMap points={live?.live_geo} />
          </div>
          {data?.isp_analysis?.length > 0 && (
            <div className="card" style={{ marginTop: 12 }}>
              <Section title="Networks your traffic reaches" />
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={data.isp_analysis.slice(0, 10)} layout="vertical" margin={{ left: 10, right: 10 }}>
                  <XAxis type="number" stroke="transparent" tick={{ fill: 'var(--text-3)', fontSize: 11 }} />
                  <YAxis type="category" dataKey="geo_isp" width={160} tick={{ fill: 'var(--text-2)', fontSize: 11 }} stroke="transparent" />
                  <Tooltip contentStyle={TT_STYLE} />
                  <Bar dataKey="connections" fill="#4a9ef7" radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </>}

        {/* ── analysis ── */}
        {tab === 'analysis' && <>
          <div className="cols">
            <div className="col col-wide">
              <div className="card">
                <Section title="Speed — hour × day" />
                <Heatmap data={data?.heatmap} />
              </div>
              <div className="card" style={{ marginTop: 12 }}>
                <Section title="By time of day" />
                {data?.time_comparison?.length
                  ? <ResponsiveContainer width="100%" height={160}>
                      <BarChart data={data.time_comparison} margin={{ left: -10, right: 10 }}>
                        <XAxis dataKey="tod" tick={{ fill: 'var(--text-2)', fontSize: 12 }} stroke="transparent" />
                        <YAxis tick={{ fill: 'var(--text-3)', fontSize: 11 }} stroke="transparent" width={36} />
                        <Tooltip contentStyle={TT_STYLE} formatter={v => [`${v.toFixed(2)} Mbps`]} />
                        <Bar dataKey="median" radius={[3, 3, 0, 0]}>
                          {data.time_comparison.map((e, i) => {
                            const min = Math.min(...data.time_comparison.map(t => t.median))
                            return <Cell key={i} fill={e.median === min ? 'var(--red)' : '#4a9ef7'} />
                          })}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  : <p className="empty">Log across morning / afternoon / evening / night.</p>}
              </div>
            </div>

            <div className="col">
              <div className="card">
                <Section title="VPN comparison" />
                {data?.vpn_comparison
                  ? <>
                      {[['Off', data.vpn_comparison.vpn_off_median, 'var(--text)'],
                        ['On',  data.vpn_comparison.vpn_on_median,  'var(--green)']].map(([lbl, val, col]) => {
                          const pct = Math.min(100, val / Math.max(data.vpn_comparison.vpn_off_median, data.vpn_comparison.vpn_on_median, 0.01) * 100)
                          return (
                            <div key={lbl} className="vpn-row">
                              <span className="vpn-label">VPN {lbl}</span>
                              <div className="vpn-track">
                                <div className="vpn-fill" style={{ width: `${pct}%`, background: col }} />
                              </div>
                              <span className="vpn-val mono">{val.toFixed(2)}</span>
                            </div>
                          )
                        })}
                      <p className="text-3" style={{ marginTop: 10 }}>
                        p = {data.vpn_comparison.p_value} · n {data.vpn_comparison.vpn_on_n}/{data.vpn_comparison.vpn_off_n}
                        &nbsp;{data.vpn_comparison.significant ? '· significant' : '· not significant'}
                      </p>
                    </>
                  : <p className="empty">Turn VPN on and off while the app runs.</p>}
              </div>

              <div className="card" style={{ marginTop: 12 }}>
                <Section title="WiFi vs hotspot" />
                {data?.connection_comparison && Object.keys(data.connection_comparison).length > 1
                  ? Object.entries(data.connection_comparison).map(([k, v]) => (
                      <div key={k} className="vpn-row">
                        <span className="vpn-label">{k}</span>
                        <div className="vpn-track">
                          <div className="vpn-fill" style={{
                            width: `${Math.min(100, v.median / Math.max(...Object.values(data.connection_comparison).map(x => x.median), 0.01) * 100)}%`,
                            background: '#4a9ef7',
                          }} />
                        </div>
                        <span className="vpn-val mono">{v.median}</span>
                      </div>
                    ))
                  : <p className="empty">Use both WiFi and hotspot to compare.</p>}
              </div>

              <div className="card" style={{ marginTop: 12 }}>
                <Section title="All alerts" />
                {data?.alerts?.map((a, i) => <AlertRow key={i} a={a} />) || <p className="empty">Loading…</p>}
              </div>
            </div>
          </div>
        </>}

        {/* ── apps ── */}
        {tab === 'apps' && (
          <div className="card">
            <Section title="Apps using your network" right={`${live?.active_count ?? 0} open sockets`} />
            <AppList apps={live?.apps} />
          </div>
        )}

      </main>

      <footer className="foot">
        Passive · metadata only · no site probing
        &nbsp;·&nbsp;{live?.label || '—'}
        &nbsp;·&nbsp;{live?.ts ? new Date(live.ts).toLocaleTimeString() : '—'}
      </footer>
    </div>
  )
}

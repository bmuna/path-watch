import { useState, useEffect, useRef, useCallback } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts'
import './App.css'

const WS_URL = 'ws://localhost:8000/ws/live'
const API = 'http://localhost:8000/api'

function useWebSocket(url) {
  const [data, setData] = useState(null)
  const ws = useRef(null)

  useEffect(() => {
    function connect() {
      ws.current = new WebSocket(url)
      ws.current.onmessage = (e) => setData(JSON.parse(e.data))
      ws.current.onclose = () => setTimeout(connect, 2000)
      ws.current.onerror = () => ws.current?.close()
    }
    connect()
    return () => ws.current?.close()
  }, [url])

  return data
}

function Kpi({ label, value, color }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={color ? { color } : {}}>{value || '—'}</div>
    </div>
  )
}

function AlertCard({ alert }) {
  const colors = { danger: '#ff6b6b', warning: '#fcc419', info: '#4dabf7', success: '#51cf66' }
  const bg = { danger: '#2a1215', warning: '#2a2412', info: '#12202a', success: '#122a15' }
  return (
    <div className="alert" style={{ borderLeft: `3px solid ${colors[alert.level] || '#4dabf7'}`, background: bg[alert.level] || bg.info }}>
      <div className="alert-title" style={{ color: colors[alert.level] }}>{alert.title}</div>
      <div className="alert-detail">{alert.detail}</div>
    </div>
  )
}

function HeatmapGrid({ data }) {
  if (!data || data.length === 0) return <div className="empty">Heatmap needs data across multiple hours. Keep logging.</div>
  const hours = [...new Set(data.map(d => d.hour))].sort((a, b) => a - b)
  const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
  const present = new Set(data.map(d => d.day))
  const activeDays = days.filter(d => present.has(d))
  if (activeDays.length === 0) return <div className="empty">Need data across different days.</div>

  const vals = data.map(d => d.value)
  const maxVal = Math.max(...vals, 0.1)
  const lookup = {}
  data.forEach(d => { lookup[`${d.hour}-${d.day}`] = d.value })

  return (
    <div className="heatmap">
      <div className="heatmap-row heatmap-header">
        <div className="heatmap-label"></div>
        {activeDays.map(d => <div key={d} className="heatmap-cell header">{d.slice(0, 3)}</div>)}
      </div>
      {hours.map(h => (
        <div key={h} className="heatmap-row">
          <div className="heatmap-label">{String(h).padStart(2, '0')}:00</div>
          {activeDays.map(d => {
            const v = lookup[`${h}-${d}`]
            const intensity = v != null ? Math.min(v / maxVal, 1) : 0
            const bg = v != null
              ? `rgba(77, 171, 247, ${0.15 + intensity * 0.75})`
              : 'rgba(255,255,255,0.03)'
            return (
              <div key={d} className="heatmap-cell" style={{ background: bg }}
                   title={v != null ? `${v.toFixed(2)} Mbps` : 'no data'}>
                {v != null ? v.toFixed(1) : ''}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function AppTable({ apps }) {
  if (!apps || apps.length === 0) return <div className="empty">Open a browser or app — they appear here.</div>
  return (
    <div className="app-table">
      {apps.map((a, i) => (
        <div key={a.name + i} className="app-row">
          <div className="app-icon">{a.name.charAt(0).toUpperCase()}</div>
          <div className="app-info">
            <div className="app-name">{a.name}</div>
            <div className="app-sub">{a.conns} socket{a.conns !== 1 ? 's' : ''}{a.top_hosts?.[0] ? ` · ${a.top_hosts[0]}` : ''}</div>
          </div>
          <div className="app-size">{a.total_fmt || '0 B'}</div>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const live = useWebSocket(WS_URL)
  const [analysis, setAnalysis] = useState(null)
  const [speedHistory, setSpeedHistory] = useState([])
  const histRef = useRef([])

  const fetchAnalysis = useCallback(() => {
    fetch(`${API}/analysis`).then(r => r.json()).then(setAnalysis).catch(() => {})
  }, [])

  useEffect(() => { fetchAnalysis(); const t = setInterval(fetchAnalysis, 15000); return () => clearInterval(t) }, [fetchAnalysis])

  useEffect(() => {
    if (!live) return
    const entry = { t: histRef.current.length, down: live.down_mbps, up: live.up_mbps }
    histRef.current = [...histRef.current.slice(-89), entry]
    setSpeedHistory([...histRef.current])
  }, [live])

  const vpnOn = live?.vpn === 'vpn'

  return (
    <div className="app">
      <header className="header">
        <div className="brand">Path Watch</div>
        <div className="header-sub">Live network intelligence · no website probing · metadata only</div>
        <div className="header-right">
          {live?.ts ? new Date(live.ts).toLocaleTimeString() : '—'}
          <span className={`dot ${live ? 'live' : ''}`}></span>
        </div>
      </header>

      <div className="kpi-row">
        <Kpi label="Download" value={live ? `${live.down_mbps.toFixed(2)} Mbps` : '—'} />
        <Kpi label="Upload" value={live ? `${live.up_mbps.toFixed(2)} Mbps` : '—'} />
        <Kpi label="VPN" value={vpnOn ? 'ON' : 'OFF'} color={vpnOn ? '#51cf66' : '#ff6b6b'} />
        <Kpi label="Connection" value={live?.connection?.toUpperCase() || '—'} />
        <Kpi label="Time" value={live?.tod || '—'} />
        <Kpi label="Public IP" value={live?.public_ip || '—'} />
        <Kpi label="Active" value={live ? `${live.active_count} sockets` : '—'} />
        <Kpi label="Session" value={live?.session_total_fmt || '0 B'} />
      </div>

      <div className="grid-2">
        <div className="panel">
          <h3>Live Speed</h3>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={speedHistory}>
              <defs>
                <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4dabf7" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4dabf7" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#fcc419" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#fcc419" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
              <XAxis dataKey="t" hide />
              <YAxis stroke="#6b7b8d" tick={{ fontSize: 11 }} tickFormatter={v => `${v}`} width={40} />
              <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }}
                       labelStyle={{ display: 'none' }}
                       formatter={(v, name) => [`${v.toFixed(2)} Mbps`, name === 'down' ? 'Download' : 'Upload']} />
              <Area type="monotone" dataKey="down" stroke="#4dabf7" fill="url(#gDown)" strokeWidth={2} dot={false} />
              <Area type="monotone" dataKey="up" stroke="#fcc419" fill="url(#gUp)" strokeWidth={2} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
          <div className="chart-legend">
            <span><span className="legend-dot" style={{ background: '#4dabf7' }}></span> Download</span>
            <span><span className="legend-dot" style={{ background: '#fcc419' }}></span> Upload</span>
            <span className="muted">Mbps · last 90 samples</span>
          </div>
        </div>

        <div className="panel">
          <h3>Throttling Alerts</h3>
          {analysis?.alerts?.length > 0
            ? analysis.alerts.map((a, i) => <AlertCard key={i} alert={a} />)
            : <div className="empty">Analyzing…</div>}
          <button className="btn" onClick={fetchAnalysis}>Refresh Analysis</button>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <h3>Speed Heatmap <span className="muted">(hour × day)</span></h3>
          <HeatmapGrid data={analysis?.heatmap} />
        </div>
        <div className="panel">
          <h3>VPN Comparison</h3>
          {analysis?.vpn_comparison ? (
            <div className="vpn-compare">
              <div className="vpn-bars">
                <div className="vpn-bar-group">
                  <div className="vpn-bar-label">VPN OFF</div>
                  <div className="vpn-bar" style={{ width: '100%', background: '#ff6b6b' }}></div>
                  <div className="vpn-bar-val">{analysis.vpn_comparison.vpn_off_median.toFixed(2)} Mbps</div>
                </div>
                <div className="vpn-bar-group">
                  <div className="vpn-bar-label">VPN ON</div>
                  <div className="vpn-bar" style={{
                    width: `${Math.min(100, (analysis.vpn_comparison.vpn_on_median / Math.max(analysis.vpn_comparison.vpn_off_median, 0.01)) * 100)}%`,
                    background: '#51cf66'
                  }}></div>
                  <div className="vpn-bar-val">{analysis.vpn_comparison.vpn_on_median.toFixed(2)} Mbps</div>
                </div>
              </div>
              <div className="vpn-stat">
                p = {analysis.vpn_comparison.p_value} · {analysis.vpn_comparison.significant ? 'statistically significant' : 'not significant'}
                <br />n = {analysis.vpn_comparison.vpn_on_n} (on) / {analysis.vpn_comparison.vpn_off_n} (off)
              </div>
            </div>
          ) : <div className="empty">Need both VPN on and off sessions to compare.</div>}

          <h3 style={{ marginTop: 20 }}>By Time of Day</h3>
          {analysis?.time_comparison?.length > 0 ? (
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={analysis.time_comparison}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
                <XAxis dataKey="tod" stroke="#6b7b8d" tick={{ fontSize: 11 }} />
                <YAxis stroke="#6b7b8d" tick={{ fontSize: 11 }} width={40} />
                <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }}
                         formatter={(v) => [`${v.toFixed(2)} Mbps`]} />
                <Bar dataKey="median" radius={[4, 4, 0, 0]}>
                  {analysis.time_comparison.map((e, i) => (
                    <Cell key={i} fill={e.median === Math.min(...analysis.time_comparison.map(t => t.median)) ? '#ff6b6b' : '#4dabf7'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="empty">Log across morning / afternoon / evening / night.</div>}
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <h3>Destinations <span className="muted">(service families)</span></h3>
          {analysis?.destinations?.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={analysis.destinations} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
                <XAxis type="number" stroke="#6b7b8d" tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="family" stroke="#6b7b8d" tick={{ fontSize: 11 }} width={120} />
                <Tooltip contentStyle={{ background: '#1c2128', border: '1px solid #3d4654', borderRadius: 8 }} />
                <Bar dataKey="connections" fill="#4dabf7" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="empty">Browse normally — destinations appear as your apps connect.</div>}
        </div>
        <div className="panel">
          <h3>Apps Using Network</h3>
          <AppTable apps={live?.apps} />
        </div>
      </div>

      <div className="panel conditions-row">
        <h3>Conditions Logged</h3>
        {analysis?.conditions?.length > 0 ? (
          <div className="conditions-grid">
            {analysis.conditions.map((c, i) => (
              <div key={i} className="condition-card">
                <div className="condition-label">{c.label}</div>
                <div className="condition-val">↓ {c.down_median} Mbps</div>
                <div className="condition-sub">n = {c.n}</div>
              </div>
            ))}
          </div>
        ) : <div className="empty">Switch VPN / hotspot / time to generate contrasting conditions.</div>}
      </div>

      <footer className="footer">
        Timing metadata only · no payloads · no site probing · {live?.label || '—'}
      </footer>
    </div>
  )
}

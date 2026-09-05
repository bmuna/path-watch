import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { MapContainer, TileLayer, CircleMarker, Circle, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
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
    let alive = true
    let timer = null
    function open() {
      if (!alive) return
      const sock = new WebSocket(WS)
      ws.current = sock
      sock.onmessage = e => { if (alive) setD(JSON.parse(e.data)) }
      sock.onclose = () => {
        if (!alive) return
        timer = setTimeout(open, 2000)
      }
      sock.onerror = () => { try { sock.close() } catch { /* ignore */ } }
    }
    open()
    return () => {
      alive = false
      clearTimeout(timer)
      try { ws.current?.close() } catch { /* ignore */ }
      ws.current = null
    }
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
function Heatmap({ data, invert, unit }) {
  if (!data?.length) return <p className="empty">Needs data across multiple hours and days.</p>
  const dayMap = { Monday:'Mon', Tuesday:'Tue', Wednesday:'Wed', Thursday:'Thu', Friday:'Fri', Saturday:'Sat', Sunday:'Sun' }
  const ORDER  = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
  const hours  = [...new Set(data.map(d => d.hour))].sort((a, b) => a - b)
  const days   = ORDER.filter(s => data.some(d => (dayMap[d.day] || d.day) === s))
  const lookup = {}
  data.forEach(d => { lookup[`${d.hour}-${dayMap[d.day] || d.day}`] = d.value })
  const maxV   = Math.max(...data.map(d => d.value), 0.01)
  const suffix = unit || 'Mbps'

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
            const hot  = v != null && (invert ? frac > 0.55 : frac < 0.45)
            const bg   = v != null
              ? `rgba(${hot ? '255,107,107' : '74,158,247'},${0.08 + frac * 0.72})`
              : 'rgba(255,255,255,0.02)'
            return (
              <div key={d} className="hm-cell hm-data" style={{ background: bg }} title={v != null ? `${v.toFixed(2)} ${suffix}` : ''}>
                {v != null ? (invert ? v.toFixed(2) : v.toFixed(1)) : ''}
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

const DEFAULT_VANTAGE = [9.0245, 38.7485]
const ADDIS_BOUNDS = [[8.88, 38.66], [9.12, 38.92]]

function numLatLon(lat, lon) {
  const a = Number(lat), b = Number(lon)
  return Number.isFinite(a) && Number.isFinite(b) ? [a, b] : null
}

/** Traffic-app palette: green → yellow → orange → purple */
const HEAT_STOPS = [
  [0.00, [46, 180, 90]],
  [0.35, [232, 197, 71]],
  [0.55, [240, 112, 48]],
  [0.75, [220, 50, 80]],
  [1.00, [124, 58, 237]], // purple throttle core
]

function heatColorAt(t) {
  const x = Math.min(1, Math.max(0, t))
  for (let i = 1; i < HEAT_STOPS.length; i++) {
    const [a, ca] = HEAT_STOPS[i - 1]
    const [b, cb] = HEAT_STOPS[i]
    if (x <= b) {
      const u = (x - a) / (b - a || 1)
      return [
        Math.round(ca[0] + (cb[0] - ca[0]) * u),
        Math.round(ca[1] + (cb[1] - ca[1]) * u),
        Math.round(ca[2] + (cb[2] - ca[2]) * u),
      ]
    }
  }
  return HEAT_STOPS[HEAT_STOPS.length - 1][1]
}

function makeHeatLayer(latlngs) {
  const Heat = L.Layer.extend({
    onAdd(map) {
      this._map = map
      const pane = map.getPanes().overlayPane
      this._canvas = L.DomUtil.create('canvas', 'pathwatch-heat')
      this._canvas.style.pointerEvents = 'none'
      pane.insertBefore(this._canvas, pane.firstChild)
      map.on('moveend viewreset zoomend resize', this._redraw, this)
      this._redraw()
    },
    onRemove(map) {
      L.DomUtil.remove(this._canvas)
      map.off('moveend viewreset zoomend resize', this._redraw, this)
    },
    _redraw() {
      const map = this._map
      if (!map || !this._canvas) return
      const size = map.getSize()
      const topLeft = map.containerPointToLayerPoint([0, 0])
      L.DomUtil.setPosition(this._canvas, topLeft)
      const w = size.x
      const h = size.y
      this._canvas.width = w
      this._canvas.height = h
      const ctx = this._canvas.getContext('2d')
      ctx.clearRect(0, 0, w, h)

      const z = map.getZoom()
      const radius = z >= 11
        ? Math.max(22, Math.min(48, 14 + (z - 10) * 6))
        : Math.max(14, Math.min(32, 8 + z * 2))

      // 1) draw intensity as black alpha (no additive white blowout)
      ctx.globalCompositeOperation = 'source-over'
      for (const p of this._latlngs || []) {
        const ll = numLatLon(p[0], p[1])
        if (!ll) continue
        const pt = map.latLngToContainerPoint(ll)
        const inten = Math.min(1, Math.max(0.08, Number(p[2]) || 0.3))
        const g = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, radius)
        g.addColorStop(0, `rgba(0,0,0,${0.55 * inten + 0.08})`)
        g.addColorStop(0.55, `rgba(0,0,0,${0.22 * inten})`)
        g.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.fillStyle = g
        ctx.beginPath()
        ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2)
        ctx.fill()
      }

      // 2) colorize alpha → green/yellow/red/purple (like traffic heatmaps)
      const img = ctx.getImageData(0, 0, w, h)
      const d = img.data
      for (let i = 0; i < d.length; i += 4) {
        const a = d[i + 3]
        if (!a) continue
        const t = Math.min(1, a / 200)
        const [r, g, b] = heatColorAt(t)
        d[i] = r
        d[i + 1] = g
        d[i + 2] = b
        d[i + 3] = Math.min(200, Math.round(a * 1.15))
      }
      ctx.putImageData(img, 0, 0)
    },
  })
  const layer = new Heat()
  layer._latlngs = latlngs
  return layer
}

function HeatLayer({ points }) {
  const map = useMap()
  const key = (points || []).map(p => `${(+p[0]).toFixed(3)},${(+p[1]).toFixed(3)},${(+p[2] || 0).toFixed(2)}`).join('|')

  useEffect(() => {
    if (!points?.length) return
    const layer = makeHeatLayer(points)
    layer.addTo(map)
    return () => { map.removeLayer(layer) }
  }, [map, key]) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}

function ViewController({ mode, remotes }) {
  const map = useMap()
  useEffect(() => {
    const t = setTimeout(() => map.invalidateSize(), 80)
    return () => clearTimeout(t)
  }, [map])
  useEffect(() => {
    if (mode === 'area') {
      map.fitBounds(ADDIS_BOUNDS, { padding: [16, 16], maxZoom: 12 })
      return
    }
    const pts = remotes.map(r => [r.lat, r.lon]).filter(Boolean)
    if (pts.length >= 2) {
      try { map.fitBounds(pts, { padding: [40, 40], maxZoom: 4 }) } catch { map.setView([20, 10], 2) }
    } else {
      map.setView([20, 10], 2)
    }
  }, [mode, map]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

function buildHeatPoints(mode, live, data, score) {
  const pts = []
  const push = (lat, lon, inten) => {
    const ll = numLatLon(lat, lon)
    if (!ll) return
    pts.push([ll[0], ll[1], Math.min(1, Math.max(0.08, Number(inten) || 0.25))])
  }

  if (mode === 'area') {
    const city = data?.city_heat?.length ? data.city_heat : (data?.geo_heat || [])
    if (city.length) {
      for (const p of city) push(p.lat, p.lon, p.intensity)
    } else {
      // fallback until analysis loads — soft field around Addis
      const base = Math.max(0.22, (score || 0) / 100)
      const [vlat, vlon] = numLatLon(live?.vantage_lat, live?.vantage_lon) || DEFAULT_VANTAGE
      for (let i = 0; i < 40; i++) {
        const ang = (i / 40) * Math.PI * 2
        const r = 0.02 + (i % 5) * 0.012
        push(vlat + Math.cos(ang) * r, vlon + Math.sin(ang) * r * 1.1, base * (0.5 + (i % 3) * 0.15))
      }
    }
    return pts
  }

  // world: every destination this machine reached
  for (const p of data?.dest_heat || []) push(p.lat, p.lon, p.p_throttled ?? p.intensity)
  for (const g of live?.live_geo || []) push(g.lat, g.lon, Math.min(0.7, 0.2 + (g.n || 1) / 20))
  for (const p of data?.map_points || []) push(p.lat, p.lon, Math.min(0.65, 0.15 + (p.connections || 1) / 30))
  return pts
}

function linkLabel(live) {
  const conn = live?.connection || 'wifi'
  const vpn  = live?.vpn === 'vpn' ? 'VPN on' : 'no VPN'
  return `${conn} · ${vpn}`
}

function clockLabel(live) {
  const raw = live?.local_time || live?.ts
  if (!raw) return '—'
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit',
  })
}

function GeoMap({ live, data, score }) {
  const [mode, setMode] = useState('area')
  const vantage = numLatLon(live?.vantage_lat, live?.vantage_lon) || DEFAULT_VANTAGE

  const remotes = useMemo(() => {
    const out = []
    for (const g of live?.live_geo || []) {
      const ll = numLatLon(g.lat, g.lon)
      if (!ll) continue
      out.push({ ...g, lat: ll[0], lon: ll[1] })
    }
    if (!out.length) {
      for (const p of data?.map_points || []) {
        const ll = numLatLon(p.lat, p.lon)
        if (!ll) continue
        out.push({
          ip: p.remote_ip, lat: ll[0], lon: ll[1],
          city: p.city, country: p.country, isp: p.isp, n: p.connections || 1,
          p_throttled: p.p_throttled,
        })
      }
    }
    return out
  }, [live?.live_geo, data?.map_points])

  const heatPoints = useMemo(
    () => buildHeatPoints(mode, live, data, score),
    [mode, live, data, score],
  )

  const zones = mode === 'area'
    ? (data?.throttle_areas?.length ? data.throttle_areas : [])
    : []

  const sc = scoreColor(score)
  const sw = scoreWord(score)
  const destCount = data?.path_meta?.destinations ?? data?.map_points?.length ?? remotes.length
  const pNow = data?.model?.p_now ?? live?.p_throttled
  const pathNote = data?.model?.trained
    ? `Learned model · P=${pNow != null ? Number(pNow).toFixed(2) : '—'} · expected ${data?.model?.expected_now ?? live?.expected_mbps ?? '—'} Mbps`
    : data?.path_meta?.tod_slow
      ? `Slower at ${data.path_meta.tod_slow.tod}`
      : 'Training on your logs…'

  return (
    <div className="map-wrap">
      <MapContainer center={vantage} zoom={12} className="map-canvas" attributionControl>
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          maxZoom={19}
        />
        <ViewController mode={mode} remotes={remotes} />
        {heatPoints.length > 0 && <HeatLayer points={heatPoints} />}

        {zones.map((z, i) => {
          const ll = numLatLon(z.lat, z.lon)
          if (!ll) return null
          const t = Math.min(1, Number(z.intensity) || 0.3)
          return (
            <Circle
              key={`z-${i}`}
              center={ll}
              radius={z.radius_m || 3000}
              pathOptions={{
                color: 'transparent',
                weight: 0,
                fillColor: t >= 0.7 ? '#7c3aed' : t >= 0.45 ? '#6366f1' : '#3b82f6',
                fillOpacity: 0.08 + t * 0.10,
              }}
            >
              <Popup>
                <div className="map-popup">
                  <strong>{z.label || 'Path zone'}</strong>
                  <div>Addis Ababa · all destinations</div>
                  {z.tod_slow && <div className="map-popup-sub">Historically slower at {z.tod_slow.tod}</div>}
                </div>
              </Popup>
            </Circle>
          )
        })}

        <CircleMarker
          center={vantage}
          radius={8}
          pathOptions={{ color: '#fff', fillColor: '#f85149', fillOpacity: 1, weight: 2 }}
        >
          <Popup>
            <div className="map-popup">
              <strong>Your machine</strong>
              <div>{live?.vantage_city || 'Addis Ababa'}</div>
              <div className="map-popup-sub">{linkLabel(live)} · {live?.tod || ''}</div>
              <div className="map-popup-sub">{destCount} destinations scored by the model</div>
            </div>
          </Popup>
        </CircleMarker>

        {mode === 'world' && remotes.map((p) => (
          <CircleMarker
            key={p.ip || `${p.lat},${p.lon}`}
            center={[p.lat, p.lon]}
            radius={Math.min(4 + (p.n || 1), 12)}
            pathOptions={{ color: '#238636', fillColor: '#58a6ff', fillOpacity: 0.85, weight: 1 }}
          >
            <Popup>
              <div className="map-popup">
                <strong>{p.city || p.country || p.ip}</strong>
                {p.isp && <div>{p.isp}</div>}
                <div className="map-popup-sub">{p.ip} · {p.n || 1} socket{(p.n || 1) !== 1 ? 's' : ''}</div>
                {p.p_throttled != null && <div className="map-popup-sub">P(throttle) {Number(p.p_throttled).toFixed(2)}</div>}
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>

      <div className="map-hud">
        <div className="map-hud-time">{clockLabel(live)}</div>
        <div className="map-hud-row">{linkLabel(live)}{live?.tod ? ` · ${live.tod}` : ''}</div>
        {live?.ssid ? <div className="map-hud-row">{live.ssid}</div> : null}
        <div className="map-hud-row">{destCount} destinations · all sockets</div>
        <div className="map-hud-row">{pathNote}</div>
        <div className="map-hud-score" style={{ color: sc }}>{sw} · {score ?? 0}</div>
      </div>

      <div className="map-view-toggle">
        <button className={mode === 'area' ? 'on' : ''} onClick={() => setMode('area')}>Addis Ababa</button>
        <button className={mode === 'world' ? 'on' : ''} onClick={() => setMode('world')}>All destinations</button>
      </div>
    </div>
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
                {live?.expected_mbps != null && (
                  <p className="text-3" style={{ marginTop: 4 }}>
                    Expected {live.expected_mbps} Mbps
                    {live.p_throttled != null ? ` · P(throttle) ${Number(live.p_throttled).toFixed(2)}` : ''}
                  </p>
                )}
                {live?.baseline_down != null && live?.expected_mbps == null && (
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
          <div className="map-legend-row">
            <span><i className="leg-dot" style={{ background: '#f85149', borderRadius: '50%', display: 'inline-block', width: 8, height: 8, marginRight: 5 }} />Your machine</span>
            <span className="heat-scale">
              <span className="heat-scale-bar" />
              Model heat — time · link · VPN · destination (green ok → purple throttled)
            </span>
          </div>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="card-head-bar">
              <Section
                title="Addis Ababa — path heat"
                right={`${data?.path_meta?.destinations ?? live?.live_geo?.length ?? 0} destinations · ${live?.active_count ?? 0} sockets · ${live?.tod || ''} · ${live?.vpn === 'vpn' ? 'VPN' : 'no VPN'} · ${live?.connection || ''}`}
              />
            </div>
            <GeoMap live={live} data={data} score={score} />
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
                <Section title="Model" right={data?.model?.trained ? `CV AUC ${data.model.cv_auc} · R² ${data.model.r2}` : 'not trained'} />
                {data?.model?.trained ? (
                  <div className="cond-grid">
                    <div className="cond-item">
                      <span className="text-3">Labeled rows</span>
                      <span className="cond-val">{data.model.n_labeled}</span>
                      <span className="text-3">{data.model.n_pos} throttle / {data.model.n_neg} clear</span>
                    </div>
                    <div className="cond-item">
                      <span className="text-3">Now</span>
                      <span className="cond-val">{data.model.p_now != null ? Number(data.model.p_now).toFixed(2) : '—'}</span>
                      <span className="text-3">expected {data.model.expected_now ?? '—'} Mbps</span>
                    </div>
                    {Object.entries(data.model.importances || {}).slice(0, 6).map(([k, v]) => (
                      <div className="cond-item" key={k}>
                        <code className="cond-code">{k}</code>
                        <span className="cond-val">{Number(v).toFixed(3)}</span>
                        <span className="text-3">importance</span>
                      </div>
                    ))}
                  </div>
                ) : <p className="empty">Train with python train_model.py — uses speed_log.csv</p>}
              </div>
              <div className="card" style={{ marginTop: 12 }}>
                <Section title="Model P(throttle) — hour × day" />
                <Heatmap data={data?.model_heatmap} invert unit="P" />
              </div>
              <div className="card" style={{ marginTop: 12 }}>
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

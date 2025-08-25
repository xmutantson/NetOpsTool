// static/js/map.js
let map;
let airports = new Map();
let polylines = [];
let lastFlowRows = [];
let stationMarkers = [];
let stationsLayer = null;
let flowsPane = 'flows';
let stationsPane = 'stations';
let flowBeads = [];           // { marker, a:LatLng, b:LatLng, t:number }
let animRAF = 0;
let lastAnimTs = 0;

// UI helpers
const $ = (id) => document.getElementById(id);
const colorMode = () => ($('colorMode')?.value || 'cardinal');
const axisFilter = () => ($('axisFilter')?.value || 'all');
const animateDots = () => !!$('animateDots')?.checked;
const ballsMax = () => Math.max(1, Math.min(12, parseInt($('ballsMax')?.value || '6')));
const weightAtMax = () => Math.max(1, parseFloat($('weightAtMax')?.value || '10000'));

function clearLines(){
  for(const pl of polylines){ pl.remove(); }
  polylines = [];
}

// Cardinal palette (avoid green/yellow/red reserved for station status)
const CARDINAL_COLORS = { E:'#f97316', W:'#6366f1', N:'#14b8a6', S:'#ec4899' };
const MONO_COLOR = '#cbd5e1';

function widthFor(weight){
  if (!weight || weight <= 0) return 1;
  // Half the previous thickness overall, clamp ~1..8 px
  const px = Math.log10(1 + weight / 50) * 10 * 0.5;
  return Math.max(1, Math.min(8, px));
}

// Collapse multiple rows into a single origin→dest entry (sum weight/legs)
function aggregateByPair(rows){
  const agg = new Map(); // key = "ORIGIN|DEST"
  for (const r of rows || []){
    const o = (r.origin || '').toUpperCase();
    const d = (r.dest   || '').toUpperCase();
    if (!o || !d) continue;
    const k = `${o}|${d}`;
    if (!agg.has(k)){
      agg.set(k, { origin:o, dest:d, legs:0, weight_lbs:0 });
    }
    const t = agg.get(k);
    t.legs += Number(r.legs || 0);
    t.weight_lbs += Number(r.weight_lbs || 0);
  }
  return Array.from(agg.values());
}

// For each unordered airport pair, record whether we have both directions.
// key = "AAA|BBB" (lexicographically sorted), flags = {forward, reverse}
// "forward" means origin === lexicographically smaller code; "reverse" is the opposite.
function buildOppositeDirMap(rows){
  const m = new Map();
  for (const r of rows || []){
    const o = (r.origin || '').toUpperCase();
    const d = (r.dest   || '').toUpperCase();
    if (!o || !d || o === d) continue;
    const lo = o < d ? o : d;
    const hi = o < d ? d : o;
    const k = `${lo}|${hi}`;
    let e = m.get(k);
    if (!e){ e = { forward:false, reverse:false }; m.set(k, e); }
    if (o === lo) e.forward = true; else e.reverse = true;
  }
  return m;
}

async function ensureAirports(){
  if (airports.size) return;
  const r = await fetch('/api/airports');
  const rows = await r.json();
  for (const a of rows){ airports.set(a.code.toUpperCase(), [a.lat, a.lon]); }
}

// Compute primary cardinal direction from A -> B in SCREEN space
// (guarantees what you see matches the color)
function primaryCardinal(A, B){
  const p1 = map.latLngToLayerPoint(A);
  const p2 = map.latLngToLayerPoint(B);
  const dx = p2.x - p1.x;   // right is positive
  const dy = p2.y - p1.y;   // down is positive
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? 'E' : 'W';
  return dy >= 0 ? 'S' : 'N';
}

function segmentWithOffset(A, B, offsetPx){
  if (!offsetPx) return [A, B];
  const p1 = map.latLngToLayerPoint(A);
  const p2 = map.latLngToLayerPoint(B);
  const dx = p2.x - p1.x, dy = p2.y - p1.y;
  const len = Math.hypot(dx, dy) || 1;
  // Perpendicular unit normal
  const nx = -dy / len, ny = dx / len;
  const offx = nx * offsetPx, offy = ny * offsetPx;
  const A2 = map.layerPointToLatLng(L.point(p1.x + offx, p1.y + offy));
  const B2 = map.layerPointToLatLng(L.point(p2.x + offx, p2.y + offy));
  return [A2, B2];
}

// Build a map of unordered pairs -> which *cardinal directions* are present in view.
// key = "AAA|BBB" (lexicographically sorted codes), flags: {hasE,hasW,hasN,hasS}
function buildAxisPresence(rows){
  const flagsByPair = new Map();
  for (const r of rows || []){
    const o = (r.origin || '').toUpperCase();
    const d = (r.dest   || '').toUpperCase();
    if (!o || !d) continue;
    const A = airports.get(o), B = airports.get(d);
    if (!A || !B) continue;
    const A0 = L.latLng(A[0], A[1]);
    const B0 = L.latLng(B[0], B[1]);
    const card = primaryCardinal(A0, B0); // E/W/N/S in SCREEN space
    const key  = (o < d) ? `${o}|${d}` : `${d}|${o}`;
    if (!flagsByPair.has(key)) flagsByPair.set(key, {hasE:false,hasW:false,hasN:false,hasS:false});
    const f = flagsByPair.get(key);
    if (card === 'E') f.hasE = true;
    if (card === 'W') f.hasW = true;
    if (card === 'N') f.hasN = true;
    if (card === 'S') f.hasS = true;
  }
  return flagsByPair;
}

function clearBeads(){
  for (const b of flowBeads){ b.marker.remove(); }
  flowBeads = [];
  if (animRAF){ cancelAnimationFrame(animRAF); animRAF = 0; }
}

function createBeads(A, B, color, count, radius, tip){
  const beads = [];
  for (let i=0; i<count; i++){
    const t0 = (i / count) % 1;
    const m = L.circleMarker(A, {
      radius,
      color: color,
      weight: 0,
      fillColor: color,
      fillOpacity: 0.95,
      pane: flowsPane,
      interactive: true
    });
    if (tip){
      m.bindTooltip(tip, { sticky:true, opacity:0.95, direction:'auto', offset:[12,0], className:'flow-tip' });
    }
    m.addTo(map);
    beads.push({ marker: m, a: A, b: B, t: t0 });
  }
  flowBeads.push(...beads);
}

function startBeadAnimation(){
  if (!animateDots() || flowBeads.length === 0) return;
  lastAnimTs = 0;
  const SPEED_PX_PER_SEC = 90; // fixed; number-of-beads represents volume
  function step(ts){
    if (!lastAnimTs) lastAnimTs = ts;
    const dt = Math.min(0.05, (ts - lastAnimTs) / 1000); // cap for stability
    lastAnimTs = ts;
    for (const b of flowBeads){
      const p1 = map.latLngToLayerPoint(b.a);
      const p2 = map.latLngToLayerPoint(b.b);
      const dx = p2.x - p1.x, dy = p2.y - p1.y;
      const len = Math.hypot(dx, dy) || 1;
      const advance = (SPEED_PX_PER_SEC * dt) / len; // fraction of segment per tick
      b.t += advance;
      if (b.t > 1) b.t -= 1;
      const nx = p1.x + dx * b.t;
      const ny = p1.y + dy * b.t;
      b.marker.setLatLng(map.layerPointToLatLng(L.point(nx, ny)));
    }
    animRAF = requestAnimationFrame(step);
  }
  animRAF = requestAnimationFrame(step);
}

function drawFlows(rows){
  clearLines();
  clearBeads(); // avoid bead accumulation across refreshes/control changes
  // Aggregate by origin→dest so we render a single segment per city-pair
  const agg = aggregateByPair(rows);
  lastFlowRows = agg;
  // For separation: split whenever both directions exist for an unordered pair
  const opp = buildOppositeDirMap(agg);

  for (const r of agg){
    const A = airports.get(r.origin?.toUpperCase());
    const B = airports.get(r.dest?.toUpperCase());
    if (!A || !B) continue; // need both endpoints
    const weightPx = widthFor(r.weight_lbs);
    // Cardinal (based on true endpoints, not offset)
    const A0 = L.latLng(A[0], A[1]);
    const B0 = L.latLng(B[0], B[1]);
    const card = primaryCardinal(A0, B0); // 'E','W','N','S'
    // Axis filter
    const ax = axisFilter();
    if (ax !== 'all'){
      if (ax === 'east'  && card !== 'E') continue;
      if (ax === 'west'  && card !== 'W') continue;
      if (ax === 'north' && card !== 'N') continue;
      if (ax === 'south' && card !== 'S') continue;
    }
    const lineColor = (colorMode() === 'mono') ? MONO_COLOR : (CARDINAL_COLORS[card] || MONO_COLOR);
    // If both directions exist for this unordered pair, split them (axis-agnostic).
    const o = (r.origin || '').toUpperCase();
    const d = (r.dest   || '').toUpperCase();
    const key = (o < d) ? `${o}|${d}` : `${d}|${o}`;
    const dirFlags = opp.get(key) || {forward:false, reverse:false};
    const bothPresent = dirFlags.forward && dirFlags.reverse;
    let A2 = A0, B2 = B0;
    if (bothPresent){
      const z = map.getZoom?.() ?? 8;
      // Scaled separation that tightens at high zoom and never exceeds ~2× line width.
      let zoomFactor = 1 - (z - 7) * 0.07;           // z=7 →1.0, z=10 →~0.79
      zoomFactor = Math.max(0.65, Math.min(1.2, zoomFactor));
      const minSep = Math.max(3, weightPx * 0.75);
      const maxSep = Math.max(minSep, weightPx * 2);
      const sep    = Math.min(maxSep, Math.max(minSep, weightPx * 1.2 * zoomFactor));
      // Deterministic side by current cardinal: E/N = +, W/S = −
      const sgn = (card === 'E' || card === 'N') ? 1 : -1;
      [A2, B2] = segmentWithOffset(A0, B0, sgn * sep);
    }
    const pl = L.polyline([A2, B2], {
      weight: weightPx,
      color: lineColor,
      opacity: 0.9,
      pane: flowsPane
    });
    const tip = `${r.origin} → ${r.dest} (${card})\nlegs: ${r.legs}, weight: ${r.weight_lbs.toFixed(1)} lbs`;
    pl.bindTooltip(tip, { sticky:true, opacity:0.95, direction:'auto', offset:[12,0], className:'flow-tip' });
    pl.addTo(map);
    polylines.push(pl);

    // Animated beads (directional)
    if (animateDots() && r.weight_lbs > 0){
      const mx = ballsMax();
      const wMax = weightAtMax();
      const count = Math.max(1, Math.min(mx, Math.ceil((r.weight_lbs / wMax) * mx)));
      // bead diameter = 2× line width  → radius = line width (with a small floor)
      const beadRadius = Math.max(2, weightPx);
      createBeads(A2, B2, lineColor, count, beadRadius, tip);
    }
  }
  // keep markers on top (pane ordering also enforces this)
  startBeadAnimation();
}

async function refresh(){
  await ensureAirports();
  const hours = document.getElementById('hours').value;
  const url = new URL('/api/flows', window.location.origin);
  url.searchParams.set('hours', hours);
  // Ignore inbound/outbound entirely; always request all rows
  url.searchParams.set('direction', 'all');
  const r = await fetch(url.toString());
  const rows = await r.json();
  drawFlows(rows);

  // simple table
  const div = document.getElementById('flows-table');
  div.innerHTML = '<table class="tbl"><thead><tr><th>Origin</th><th>Dest</th><th>Card</th><th>Legs</th><th>Weight (lbs)</th></tr></thead><tbody></tbody></table>';
  const tb = div.querySelector('tbody');
  for (const x of aggregateByPair(rows)){
    const A = airports.get(x.origin?.toUpperCase());
    const B = airports.get(x.dest?.toUpperCase());
    const card = (A && B) ? primaryCardinal(L.latLng(A[0],A[1]), L.latLng(B[0],B[1])) : '—';
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${x.origin}</td><td>${x.dest}</td><td>${card}</td><td>${x.legs}</td><td>${x.weight_lbs.toFixed(1)}</td>`;
    tb.appendChild(tr);
  }
}

function parseIsoUtc(iso){
  if (!iso) return NaN;
  // Treat naive timestamps from server as UTC.
  if (/[zZ]|[+-]\d\d:\d\d$/.test(iso)) return Date.parse(iso);
  return Date.parse(iso + 'Z');
}
function ageSeconds(iso){
  const t = parseIsoUtc(iso);
  if (isNaN(t)) return Infinity;
  return Math.max(0, (Date.now() - t) / 1000);
}

function statusColorByAge(age){
  if (age <= 90) return '#34d399';     // online (green)
  if (age <= 300) return '#f59e0b';    // idle (yellow)
  return '#ef4444';                    // offline (red)
}

function markerRadiusForZoom(z){
  // 3× bigger than before, still scales with zoom
  const base = 4 + (z - 6) * 1.2;            // was ~5..12
  const px = base * 3;                        // now ~15..36
  return Math.max(12, Math.min(30, px));     // clamp for sanity
}

async function drawStations(){
  const r = await fetch('/api/stations');
  const rows = await r.json();
  await ensureAirports();

  if (stationsLayer){
    stationsLayer.remove();
    stationMarkers = [];
  }
  stationsLayer = L.layerGroup(undefined, {pane: stationsPane});

  const z = map.getZoom();
  const rad = markerRadiusForZoom(z);
  for (const st of rows){
    let lat = st.last_origin_lat, lon = st.last_origin_lon;
    if ((lat==null || lon==null) && st.last_default_origin){
      const a = airports.get(st.last_default_origin.toUpperCase());
      if (a){ lat = a[0]; lon = a[1]; }
    }
    if (lat==null || lon==null) continue;
    const age = ageSeconds(st.last_seen_at);
    const color = statusColorByAge(age);
    const m = L.circleMarker([lat, lon], {
      radius: rad,
      color: '#0b0d10',
      weight: 1.5,
      fillColor: color,
      fillOpacity: 0.95,
      pane: stationsPane
    });
    const coords = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    const status = (age<=90) ? 'online' : (age<=300 ? 'idle' : 'offline');
    m.bindPopup(
      `<strong>${st.name}</strong><br>` +
      `status: <span style="color:${color}">${status}</span><br>` +
      `last seen: ${st.last_seen_at || '—'}<br>` +
      `default origin: ${st.last_default_origin || '—'}<br>` +
      `coords: ${coords}<br>` +
      `<a href="/stations/${encodeURIComponent(st.name)}">view details</a>`
    );
    m.on('click', () => m.openPopup());
    m.addTo(stationsLayer);
    stationMarkers.push(m);
  }
  stationsLayer.addTo(map);
}

function updateMarkerSizes(){
  const z = map.getZoom();
  const r = markerRadiusForZoom(z);
  for (const m of stationMarkers){
    m.setRadius(r);
  }
  // Redraw flows to keep offset measured in pixels correct for the new zoom.
  if (lastFlowRows.length) drawFlows(lastFlowRows);
}

function renderLegendHtml(){
  if (colorMode() === 'mono'){
    return (
      `<div class="legend-row"><span class="legend-swatch" style="background:${MONO_COLOR}"></span> flow (monochrome)</div>` +
      `<hr style="border:none;border-top:1px solid #1f2a37;margin:6px 0">` +
      `<div>Animated dots indicate direction.</div>` +
      `<div class="legend-row"><span class="legend-dot dot-online"></span> online ≤ 90s</div>` +
      `<div class="legend-row"><span class="legend-dot dot-idle"></span> idle ≤ 5m</div>` +
      `<div class="legend-row"><span class="legend-dot dot-offline"></span> offline</div>`
    );
  }
  return (
    `<div class="legend-row"><span class="legend-swatch" style="background:${CARDINAL_COLORS.E}"></span> eastbound</div>` +
    `<div class="legend-row"><span class="legend-swatch" style="background:${CARDINAL_COLORS.W}"></span> westbound</div>` +
    `<div class="legend-row"><span class="legend-swatch" style="background:${CARDINAL_COLORS.N}"></span> northbound</div>` +
    `<div class="legend-row"><span class="legend-swatch" style="background:${CARDINAL_COLORS.S}"></span> southbound</div>` +
    `<hr style="border:none;border-top:1px solid #1f2a37;margin:6px 0">` +
    `<div class="legend-row"><span class="legend-dot dot-online"></span> online ≤ 90s</div>` +
    `<div class="legend-row"><span class="legend-dot dot-idle"></span> idle ≤ 5m</div>` +
    `<div class="legend-row"><span class="legend-dot dot-offline"></span> offline</div>`
  );
}

function addLegendControl(){
  if (map._netopsLegend) return;
  const Legend = L.Control.extend({
    options: { position: 'topright' },
    onAdd: function(){
      const div = L.DomUtil.create('div', 'leaflet-control legend');
      div.innerHTML = renderLegendHtml();
      map._legendDiv = div;
      return div;
    }
  });
  map._netopsLegend = new Legend();
  map._netopsLegend.addTo(map);
}

window.addEventListener('DOMContentLoaded', async () => {
  map = L.map('map').setView([47.6062, -122.3321], 7);
  // Dedicated panes so station dots always render above flow lines.
  map.createPane(flowsPane);
  map.getPane(flowsPane).style.zIndex = 410;
  map.createPane(stationsPane);
  map.getPane(stationsPane).style.zIndex = 420;
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 12
  }).addTo(map);

  addLegendControl();
  document.getElementById('refresh').addEventListener('click', refresh);
  // React to control changes without full page reload
  ['axisFilter','colorMode','animateDots','ballsMax','weightAtMax','direction','hours'].forEach(id => {
    const el = $(id);
    if (el){
      el.addEventListener('change', async () => {
        if (map._legendDiv) map._legendDiv.innerHTML = renderLegendHtml();
        await refresh();
      });
    }
  });

  await drawStations();
  await refresh();
  map.on('zoomend', () => {
    updateMarkerSizes();
  });
});

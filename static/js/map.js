// static/js/map.js
let map, airports = new Map(), polylines = [];

function clearLines(){
  for(const pl of polylines){ pl.remove(); }
  polylines = [];
}

function lineColor(direction){
  return direction === 'inbound' ? '#34d399' : '#60a5fa';
}

function widthFor(weight){
  if (!weight || weight <= 0) return 1;
  // 1..8px range (log-ish)
  return Math.max(1, Math.min(8, Math.log10(1+weight/100)));
}

async function ensureAirports(){
  if (airports.size) return;
  const r = await fetch('/api/airports');
  const rows = await r.json();
  for (const a of rows){ airports.set(a.code.toUpperCase(), [a.lat, a.lon]); }
}

function drawFlows(rows){
  clearLines();
  for (const r of rows){
    const A = airports.get(r.origin?.toUpperCase());
    const B = airports.get(r.dest?.toUpperCase());
    if (!A || !B) continue; // need both endpoints
    const pl = L.polyline([A, B], {weight: widthFor(r.weight_lbs), color: lineColor(r.direction)});
    pl.bindTooltip(`${r.origin} → ${r.dest} (${r.direction})\nlegs: ${r.legs}, weight: ${r.weight_lbs.toFixed(1)} lbs`);
    pl.addTo(map);
    polylines.push(pl);
  }
}

async function refresh(){
  await ensureAirports();
  const hours = document.getElementById('hours').value;
  const direction = document.getElementById('direction').value;
  const url = new URL('/api/flows', window.location.origin);
  url.searchParams.set('hours', hours);
  url.searchParams.set('direction', direction);
  const r = await fetch(url.toString());
  const rows = await r.json();
  drawFlows(rows);

  // simple table
  const div = document.getElementById('flows-table');
  div.innerHTML = '<table class="tbl"><thead><tr><th>Origin</th><th>Dest</th><th>Dir</th><th>Legs</th><th>Weight (lbs)</th></tr></thead><tbody></tbody></table>';
  const tb = div.querySelector('tbody');
  for (const x of rows){
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${x.origin}</td><td>${x.dest}</td><td>${x.direction}</td><td>${x.legs}</td><td>${x.weight_lbs.toFixed(1)}</td>`;
    tb.appendChild(tr);
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  map = L.map('map').setView([47.6062, -122.3321], 7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 12
  }).addTo(map);

  document.getElementById('refresh').addEventListener('click', refresh);
  refresh();
});

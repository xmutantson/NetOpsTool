const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const script = file => read(file).match(/<script>([\s\S]*?)<\/script>/)[1];

// Minimal DOM: assigning HTML is forbidden so feeder text must use text nodes.
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.value = '';
    this.checked = false;
    this._text = '';
  }
  set innerHTML(value) { throw new Error('Unexpected HTML rendering'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  appendChild(child) {
    if (child.tagName === '#FRAGMENT') this.children.push(...child.children);
    else this.children.push(child);
    return child;
  }
  replaceChildren(...children) { this._text = ''; this.children = []; children.forEach(x => this.appendChild(x)); }
  addEventListener() {}
}

function harness() {
  let now = Date.parse('2026-10-02T12:00:00Z');
  const intervals = new Map(), timeouts = new Map(), elements = new Map();
  let id = 0;
  const listeners = {};
  const document = {
    createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element('#fragment'),
    getElementById(key) {
      if (!elements.has(key)) elements.set(key, new Element('div'));
      return elements.get(key);
    },
    querySelector: key => document.getElementById(key)
  };
  const context = vm.createContext({
    document, URL, AbortController,
    Date: class extends Date { static now() { return now; } },
    window: { location: { origin: 'http://localhost' }, addEventListener: (name, fn) => {
      if (!listeners[name]) listeners[name] = [];
      listeners[name].push(fn);
    } },
    setInterval: (fn, ms) => { intervals.set(++id, {fn, ms}); return id; },
    clearInterval: key => intervals.delete(key),
    setTimeout: (fn, ms) => { timeouts.set(++id, {fn, ms}); return id; },
    clearTimeout: key => timeouts.delete(key),
    requestAnimationFrame: () => ++id,
    cancelAnimationFrame() {},
    fetch: async () => { throw new Error('Unexpected network call'); }
  });
  vm.runInContext(script('templates/base.html'), context);
  return {
    context, document, intervals, timeouts, listeners,
    emit: (name, event = {}) => (listeners[name] || []).forEach(fn => fn(event)),
    run: source => vm.runInContext(source, context),
    advance: ms => { now += ms; }
  };
}

function mapHarness() {
  const h = harness();
  const shapes = [];
  function shape(points, options) {
    const obj = {
      points, options: {...options}, removed: false,
      bindTooltip(tip) { this.tip = tip; return this; },
      bindPopup(popup) { this.popup = popup; return this; },
      addTo() { return this; },
      remove() { this.removed = true; },
      setStyle(style) { Object.assign(this.options, style); },
      setRadius(radius) { this.options.radius = radius; },
      on() {}, openPopup() {}
    };
    shapes.push(obj);
    return obj;
  }
  h.context.L = {
    latLng: (lat, lng) => ({lat, lng}), point: (x, y) => ({x, y}),
    polyline: shape, circleMarker: shape,
    layerGroup: () => ({addTo() {}, remove() {}})
  };
  vm.runInContext(read('static/js/map.js'), h.context);
  h.run(`map = {
    latLngToLayerPoint: a => ({x:a.lng, y:a.lat}),
    layerPointToLatLng: p => ({lat:p.y, lng:p.x}),
    getZoom: () => 8
  };`);
  return {...h, shapes};
}

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const flush = () => new Promise(resolve => setImmediate(resolve));

test('pure offsets separate reverse horizontal, vertical and diagonal routes', () => {
  const h = mapHarness();
  for (const [a, b] of [ [{x:0,y:0},{x:100,y:0}], [{x:0,y:0},{x:0,y:100}], [{x:1,y:2},{x:80,y:60}] ]) {
    const forward = h.context.offsetPoints(a, b, 6);
    const reverse = h.context.offsetPoints(b, a, 6);
    const midpoint = route => ({x:(route[0].x + route[1].x)/2, y:(route[0].y + route[1].y)/2});
    const f = midpoint(forward), r = midpoint(reverse);
    assert.ok(Math.abs(Math.hypot(f.x-r.x, f.y-r.y) - 12) < 1e-9);
    assert.ok(Math.abs((f.x+r.x)/2 - (a.x+b.x)/2) < 1e-9);
    assert.ok(Math.abs((f.y+r.y)/2 - (a.y+b.y)/2) < 1e-9);
  }
  assert.equal(h.context.offsetPoints({x:2,y:3}, {x:2,y:3}, 5)[0].x, 2);
});

test('drawn opposite routes use distinct lanes regardless of order or weight', () => {
  const h = mapHarness();
  h.run(`airports = new Map([['AAA', [0,0]], ['BBB', [0,100]]]);`);
  const routes = [{origin:'AAA', dest:'BBB', legs:1, weight_lbs:100}, {origin:'BBB', dest:'AAA', legs:2, weight_lbs:5000}];
  for (const rows of [routes, [...routes].reverse()]) {
    h.context.drawFlows(rows);
    const current = h.shapes.filter(s => !s.removed);
    assert.equal(current.length, 2);
    assert.ok(current[0].points[0].lat * current[1].points[0].lat < 0);
  }
});

test('refresh discards superseded results and coalesces queued requests', async () => {
  const h = harness(), jobs = [], rendered = [];
  let concurrent = 0, maximum = 0;
  const c = h.context.createRefreshController({
    status:h.document.getElementById('status'),
    load: signal => {
      concurrent++; maximum = Math.max(maximum, concurrent);
      const job = deferred(); jobs.push({...job, signal});
      return job.promise.finally(() => { concurrent--; });
    },
    render: data => rendered.push(data)
  });
  const pending = c.request();
  c.request(); c.request();
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].signal.aborted, true);
  jobs[0].resolve('old filter');
  await flush();
  assert.equal(jobs.length, 2);
  assert.deepEqual(rendered, []);
  jobs[1].resolve('latest filter');
  await pending;
  assert.deepEqual(rendered, ['latest filter']);
  assert.equal(maximum, 1);
  assert.equal(h.document.getElementById('status').dataset.state, 'current');
  c.stop();
  assert.equal(h.intervals.size, 0);
});

test('failed refresh preserves good content, marks stale, then recovers', async () => {
  const h = harness(), status = h.document.getElementById('status');
  let fail = false, content = '';
  const c = h.context.createRefreshController({status,
    load: async () => { if (fail) throw new Error('HTTP 503'); return 'good snapshot'; },
    render: data => { content = data; }
  });
  await c.request();
  fail = true;
  await c.request();
  assert.equal(content, 'good snapshot');
  assert.equal(status.dataset.state, 'stale');
  assert.match(status.textContent, /HTTP 503/);
  assert.match(status.textContent, /Last refreshed:/);
  fail = false;
  await c.request();
  assert.equal(status.dataset.state, 'current');
  h.advance(61000);
  [...h.intervals.values()].find(t => t.ms === 5000).fn();
  assert.equal(status.dataset.state, 'stale');
  c.stop();
});

test('timeout reports stale and polling retries without concurrent cycles', async () => {
  const h = harness(), status = h.document.getElementById('status');
  let loads = 0;
  const c = h.context.createRefreshController({status,
    load: signal => { loads++; return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')))); },
    render() { assert.fail('timed out request must not render'); }
  });
  const pending = c.request();
  const poll = [...h.intervals.values()].find(t => t.ms === 30000);
  poll.fn();
  assert.equal(loads, 1);
  [...h.timeouts.values()][0].fn();
  await pending;
  assert.match(status.textContent, /request timed out/);
  poll.fn();
  assert.equal(loads, 2);
  c.stop();
  await flush();
});

test('fetchRows rejects HTTP failures and invalid envelopes and avoids cached responses', async () => {
  const h = harness();
  let options;
  h.context.fetch = async (url, opts) => { options = opts; return {ok:false, status:503}; };
  await assert.rejects(h.context.fetchRows('/api/stations'), /HTTP 503/);
  assert.equal(options.cache, 'no-store');
  h.context.fetch = async () => ({ok:true, json:async () => ({error:'unavailable'})});
  await assert.rejects(h.context.fetchRows('/api/stations'), /Unexpected response/);
});

test('map loads all resources each refresh using the latest recency value', async () => {
  const h = mapHarness(), urls = [];
  h.document.getElementById('hours').value = '6';
  h.context.fetch = async url => { urls.push(String(url)); return {ok:true, json:async () => []}; };
  await h.context.loadMapData(new AbortController().signal);
  h.document.getElementById('hours').value = '72';
  await h.context.loadMapData(new AbortController().signal);
  assert.equal(urls.filter(x => x === '/api/airports').length, 2);
  assert.equal(urls.filter(x => x === '/api/stations').length, 2);
  assert.match(urls[2], /hours=6/);
  assert.match(urls[5], /hours=72/);
  assert.ok(urls.every(x => !x.includes('direction=')));
});

test('station age colors and popup status advance without another request', () => {
  const h = mapHarness();
  h.context.drawStations([{name:'Hub & Annex', last_seen_at:'2026-10-02T12:00:00', last_origin_lat:1, last_origin_lon:2}]);
  const marker = h.shapes[0];
  assert.equal(marker.options.fillColor, '#34d399');
  h.advance(91000); h.context.updateStationAges();
  assert.equal(marker.options.fillColor, '#f59e0b');
  assert.match(marker.popup.textContent, /status: idle/);
  h.advance(210000); h.context.updateStationAges();
  assert.equal(marker.options.fillColor, '#ef4444');
  assert.match(marker.popup.textContent, /status: offline/);
  assert.equal(h.context.ageSeconds(null), Infinity);
  assert.equal(h.context.ageSeconds('2026-10-02T13:00:00Z'), 0);
});

test('naive UTC, Z and explicit positive or negative offsets represent the same instant', () => {
  const h = mapHarness();
  const expected = Date.parse('2026-10-02T12:00:00Z');
  for (const timestamp of [
    '2026-10-02T12:00:00',
    '2026-10-02T12:00:00Z',
    '2026-10-02T14:00:00+02:00',
    '2026-10-02T05:00:00-07:00'
  ]) {
    assert.equal(h.context.parseIsoUtc(timestamp), expected, timestamp);
    assert.equal(h.context.ageSeconds(timestamp), 0, timestamp);
  }
});

test('map polling and age timers survive bfcache navigation and stop on final unload', async () => {
  const h = mapHarness();
  const map = h.run('map');
  Object.assign(map, {
    setView() { return this; }, createPane() {}, getPane() { return {style:{}}; }, on() {}
  });
  h.context.L.map = () => map;
  h.context.L.tileLayer = () => ({addTo() {}});
  h.run('addLegendControl = () => {};');
  let requests = 0;
  h.context.fetch = async () => { requests++; return {ok:true, json:async () => []}; };
  h.emit('DOMContentLoaded');
  await flush();
  assert.equal(requests, 3);
  assert.equal(h.intervals.size, 3);
  h.emit('pagehide', {persisted:true});
  assert.equal(h.intervals.size, 3);
  h.emit('pageshow', {persisted:true});
  [...h.intervals.values()].find(t => t.ms === 30000).fn();
  await flush();
  assert.equal(requests, 6);
  h.emit('pagehide', {persisted:false});
  assert.equal(h.intervals.size, 0);
  await h.run('refresh()');
  assert.equal(requests, 6);
});

test('a failed map resource retains the entire previous view until recovery', async () => {
  const h = mapHarness(), status = h.document.getElementById('status');
  let fail = false, latitude = 1;
  h.context.fetch = async url => {
    if (String(url) === '/api/stations' && fail) return {ok:false, status:503};
    const rows = String(url) === '/api/airports'
      ? [{code:'AAA',lat:latitude,lon:2},{code:'BBB',lat:3,lon:4}]
      : String(url) === '/api/stations'
        ? [{name:'Station',last_default_origin:'AAA'}]
        : [{origin:'AAA',dest:'BBB',legs:1,weight_lbs:5}];
    return {ok:true, json:async () => rows};
  };
  const c = h.context.createRefreshController({status, load:h.context.loadMapData, render:h.context.renderMapData});
  await c.request();
  const previous = h.document.getElementById('flows-table').children[0];
  const shapeCount = h.shapes.length;
  fail = true; latitude = 11;
  await c.request();
  assert.equal(h.shapes.length, shapeCount);
  assert.equal(h.document.getElementById('flows-table').children[0], previous);
  assert.equal(h.run("airports.get('AAA')[0]"), 1);
  assert.equal(status.dataset.state, 'stale');
  fail = false;
  await c.request();
  assert.equal(h.run("airports.get('AAA')[0]"), 11);
  assert.equal(status.dataset.state, 'current');
  c.stop();
});

test('refreshed airport coordinates are used by flows and station fallback markers', () => {
  const h = mapHarness();
  const snapshot = {
    airportRows:[{code:'AAA',lat:1,lon:2},{code:'BBB',lat:3,lon:4}],
    stationRows:[{name:'Station',last_default_origin:'AAA'}],
    flowRows:[{origin:'AAA',dest:'BBB',legs:1,weight_lbs:5}]
  };
  h.context.renderMapData(snapshot);
  snapshot.airportRows[0] = {code:'AAA',lat:11,lon:12};
  h.context.renderMapData(snapshot);
  const latest = h.shapes.slice(-2);
  assert.equal(latest[0].points[0], 11);
  assert.equal(latest[1].points[0].lat, 11);
});

test('feeder fields stay literal in inventory, station tables, map popup and tooltips', () => {
  const h = mapHarness();
  const label = 'A & B <cargo>';
  vm.runInContext(script('templates/station_detail.html')
    .replace('{{ name|tojson }}', JSON.stringify(label)).split('const inventoryRefresh =')[0], h.context);
  h.context.renderInventory([{category:label,item:label,qty:2,weight_lbs:3.5,updated_at:label}]);
  const inventory = h.document.getElementById('cat-list');
  assert.equal(inventory.children[0].children[0].children[0].textContent, label);
  assert.equal(h.document.getElementById('grand-wt').textContent, '3.5');
  inventory.children[0].open = false;
  h.context.renderInventory([{category:label,item:label,qty:2,weight_lbs:3.5}]);
  assert.equal(inventory.children[0].open, false);
  vm.runInContext(script('templates/stations.html').split('const stationRefresh =')[0], h.context);
  h.context.renderStations([{name:label,last_default_origin:label}]);
  const link = h.document.getElementById('#stations-table tbody').children[0].children[0].children[0];
  assert.equal(link.textContent, label);
  assert.equal(link.href, `/stations/${encodeURIComponent(label)}`);
  h.context.drawStations([{name:label,last_origin_lat:1,last_origin_lon:2,last_default_origin:label}]);
  assert.equal(h.shapes[0].popup.children[0].textContent, label);
  h.context.renderFlowsTable([{origin:label,dest:'BBB',legs:2,weight_lbs:5}]);
  assert.ok(h.document.getElementById('flows-table').textContent.includes(label.toUpperCase()));
  h.run(`airports = new Map([['A & B <CARGO>', [0,0]], ['BBB', [0,100]]]);`);
  h.context.drawFlows([{origin:label,dest:'BBB',legs:2,weight_lbs:5}]);
  assert.ok(h.shapes.at(-1).tip.textContent.includes(label.toUpperCase()));
  h.context.renderInventory([]);
  assert.equal(h.document.getElementById('grand-wt').textContent, '0.0');
  assert.match(inventory.textContent, /No inventory/);
});

test('recency UI explains latest snapshots and has no ignored direction selector', () => {
  const html = read('templates/index.html');
  assert.match(html, /Latest snapshot within:/);
  assert.match(html, /weights are not totals over time/);
  assert.doesNotMatch(html, /id="direction"/);
});

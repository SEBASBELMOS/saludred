// Run with: node --test tests/frontend.test.cjs
// Uses no server, credentials or external database.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function app(respond) {
  const nodes = new Map();
  const element = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, open: false, textContent: '', innerHTML: '', value: '',
      addEventListener() {}, focus() {}, close() {},
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { getElementById: element, addEventListener() {}, querySelectorAll: () => [] },
    window: { addEventListener() {} },
    sessionStorage: { getItem: () => null, removeItem() {} },
    fetch: async (url) => {
      const { status = 200, body } = respond(url);
      return { status, ok: status >= 200 && status < 300, json: async () => body };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8'), context);
  return (code) => vm.runInContext(code, context);
}

const network = { organizations: [{ organization_id: 'north', organization_name: 'Norte' }] };

test('a failed institution does not become zero beds or replace the last snapshot', async () => {
  const run = app(url => url === '/api/v1/network/capacity'
    ? { body: network } : { status: 503, body: { detail: 'IPS no disponible' } });
  run('state.data.beds = [{ id: "previous-bed" }]; state.data.orgs = [{ id: "previous-org" }]');
  await assert.rejects(run('loadNetwork()'), /IPS no disponible/);
  assert.equal(run('state.data.beds[0].id'), 'previous-bed');
  assert.equal(run('state.data.orgs[0].id'), 'previous-org');
  assert.equal(run('state.data.network'), null);
});

test('a successful capacity response maps IDs, service and status used by the UI', async () => {
  const run = app(url => url === '/api/v1/network/capacity' ? { body: network } : {
    body: [{ id: 'bed-1', organization_id: 'north', code: 'N-1', name: 'Cama 1', service: 'UCI', status: 'AVAILABLE' }],
  });
  await run('loadNetwork()');
  assert.equal(run('summary(state.data.beds).available'), 1);
  assert.equal(run('state.data.beds[0].org'), 'north');
  assert.equal(run('state.data.beds[0].service'), 'UCI');
});

test('priority preview matches the API when the request has no destination', () => {
  const run = app(() => ({ body: {} }));
  run(`state.data.queue = [{ id: 'earlier', service: 'UCI', target: 'north', priority: 'EMERGENCY', requestedAt: '2026-09-23T08:00:00Z' }]`);
  assert.equal(run(`priorRequest({id:'later',service:'UCI',target:null,priority:'ROUTINE',requestedAt:'2026-09-23T09:00:00Z'}).id`), 'earlier');
  assert.equal(run(`priorRequest({id:'later',service:'UCI',target:'south',priority:'ROUTINE',requestedAt:'2026-09-23T09:00:00Z'})`), undefined);
});

test('birth dates are displayed as calendar dates without a timezone shift', () => {
  const run = app(() => ({ body: {} }));
  assert.equal(run('dateOnly("2000-01-01")'), '01/01/2000');
});

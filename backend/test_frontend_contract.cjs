// Exercise the actual TypeScript adapter with backend-generated fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../frontend-app/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../frontend-app/api/chat.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const sandbox = { exports: {}, require: () => ({ identityHeaders: async () => ({ Authorization: 'Bearer synthetic-test-installation-123456' }) }), process: { env: { EXPO_PUBLIC_API_URL: 'http://localhost:5000' } }, URL, AbortController, setTimeout, clearTimeout };
vm.runInNewContext(code, sandbox);
const [weather, places] = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.equal(sandbox.exports.adaptResult(weather).type, 'weather');
assert.equal(sandbox.exports.adaptResult(weather).description, weather.description);
assert.equal(sandbox.exports.adaptResult(places).places.length, 1);
assert.equal(sandbox.exports.adaptResult(places).status, 'ok');
assert(sandbox.exports.adaptResult(places).description.includes('Synthetic test listing'));
assert.equal(sandbox.exports.adaptResult(places).sections[0].places[0].id, 'fixture-hotel');
assert.equal(sandbox.exports.adaptResult({ ...weather, type: 'invented' }), null);
assert.equal(sandbox.exports.adaptResult({ ...weather, details: null }), null);
const renderer = fs.readFileSync(path.join(__dirname, '../frontend-app/App.tsx'), 'utf8');
// Concise summaries keep technical details in the existing result component's
// expandable details block; legacy responses still render their detail pills.
assert(renderer.includes('(result.summary ? [] : result.details).map'));
assert(fs.readFileSync(path.join(__dirname, '../frontend-app/components/CityResults.tsx'), 'utf8').includes('result.details.map'));
assert(renderer.includes('result.description'));
assert(!renderer.includes('result.places.map'));
assert(renderer.includes('<CityResults'));
(async () => {
  let request;
  sandbox.fetch = async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ success: true, data: weather, error: null }) };
  };
  const result = await sandbox.exports.sendChat('Weather today', 'Bengaluru');
  assert.equal(result.type, 'weather');
  assert.equal(request.url, 'http://localhost:5000/v1/chat');
  assert.deepEqual(JSON.parse(request.options.body), { message: 'Weather today', city: 'Bengaluru' });
  sandbox.fetch = async () => ({ ok: false, json: async () => ({ success: false, data: { ...weather, type: 'stay', places: [], status: 'service_not_configured' }, error: { code: 'service_not_configured', message: 'missing key' } }) });
  assert.equal((await sandbox.exports.sendChat('Hotel', 'Bengaluru')).status, 'service_not_configured');
  console.log('adapter contract passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

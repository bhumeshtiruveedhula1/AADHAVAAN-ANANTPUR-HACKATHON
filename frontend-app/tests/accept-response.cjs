// Live contract check: payload enters via stdin, is never printed or saved.
/* global __dirname */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../api/chat.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const sandbox = { exports: {}, URL, require: () => ({}) };
vm.runInNewContext(code, sandbox);
const envelope = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.equal(envelope.success, true);
const result = sandbox.exports.adaptResult(envelope.data);
assert(result);
assert(result.places.length > 0);
assert.equal(result.places.length, envelope.data.places.length);
assert.equal(result.sections.length, envelope.data.sections.length);
for (const section of result.sections) {
  assert(section.places.length > 0);
  for (const place of section.places) {
    assert.equal(place.category, section.intent);
    assert(sandbox.exports.directionsUrl(place.directions_url, place.id));
    if (place.photo) {
      assert.equal(place.image, place.photo.url);
      assert(Array.isArray(place.photo.authors));
    }
  }
}
console.log('Live response accepted by actual adapter');

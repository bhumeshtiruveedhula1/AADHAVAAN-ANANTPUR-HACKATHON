// Real React component execution, mocked RN/Expo native surfaces and no network.
/* global __dirname */
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const ts = require('typescript');
const React = require('react');
const { create, act } = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;
const root = path.resolve(__dirname, '..');
let opened = [], alerts = [], failOpen = false, mapThrows = false;
let trees = [];
let mockFetch = async () => { throw Error('Unexpected test network request'); };
let shared = [];
let backHandler = null;
const constants = { executionEnvironment: 'storeClient', expoConfig: { extra: {} } };
const timers = new Map();
const rn = { View: 'View', Text: 'Text', TextInput: 'TextInput', Image: 'NativeImage', Pressable: 'Pressable', Platform: { OS: 'android' },
  ImageBackground: 'ImageBackground', SafeAreaView: 'SafeAreaView', KeyboardAvoidingView: 'KeyboardAvoidingView', ScrollView: 'ScrollView', Modal: 'Modal', Keyboard: { dismiss: () => {} },
  Share: { share: async (value) => { shared.push(value); } },
  BackHandler: { addEventListener: (name, callback) => { assert.equal(name, 'hardwareBackPress'); backHandler = callback; return { remove: () => { backHandler = null; } }; } },
  StyleSheet: { create: (styles) => styles }, Alert: { alert: (...args) => alerts.push(args) },
  Linking: { openURL: async (url) => { opened.push(url); if (failOpen) throw Error('Mock handler failure'); } } };
const modules = new Map();
const secureValues = new Map();
const secureStore = { getItemAsync: async (key) => secureValues.get(key) || null, setItemAsync: async (key, value) => { secureValues.set(key, value); }, deleteItemAsync: async (key) => { secureValues.delete(key); } };
// RN FormData accepts {uri,name,type}; Node's web FormData does not.
class NativeFormData {
  constructor() { this.parts = []; }
  append(name, value) { this.parts.push([name, value]); }
}
function load(filename) {
  const full = path.resolve(root, filename);
  if (modules.has(full)) return modules.get(full);
  const exports = {};
  modules.set(full, exports);
  const code = ts.transpileModule(fs.readFileSync(full, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
  const localRequire = (name) => {
    if (/\.(jpg|ttf)$/.test(name)) return 1;
    if (name === 'expo-font') return { useFonts: () => [true] };
    if (name === 'expo-status-bar') return { StatusBar: 'StatusBar' };
    if (name === 'expo-image-picker') return { requestMediaLibraryPermissionsAsync: async () => ({ granted: true }), launchImageLibraryAsync: async () => ({ canceled: true }) };
    if (name === 'expo-secure-store') return secureStore;
    if (name === 'expo-crypto') return { randomUUID: crypto.randomUUID, CryptoDigestAlgorithm: { SHA256: 'SHA256' }, digestStringAsync: async (_, value) => crypto.createHash('sha256').update(value).digest('hex') };
    if (name === 'react-native') return rn;
    if (name === 'expo-image') return { Image: 'ProviderImage' };
    if (name === 'expo-constants') return { __esModule: true, default: constants };
    if (name === 'react-native-maps') {
      if (mapThrows) throw Error('Mock missing native map module');
      return { __esModule: true, default: 'GoogleMap', Marker: 'Marker', PROVIDER_GOOGLE: 'google' };
    }
    if (name.startsWith('.')) {
      const target = path.resolve(path.dirname(full), name);
      const suffix = ['.native.tsx', '.tsx', '.ts'].find((extension) => fs.existsSync(target + extension));
      return load(target + suffix);
    }
    return require(name);
  };
  vm.runInNewContext(code, { exports, require: localRequire, process: { env: { EXPO_PUBLIC_API_URL: 'http://localhost:5000' } }, URL, console,
    AbortController, FormData: NativeFormData, Error, fetch: (...args) => mockFetch(...args),
    setTimeout: (fn) => { const id = {}; timers.set(id, fn); return id; }, clearTimeout: (id) => timers.delete(id) }, { filename: full });
  return exports;
}
const { adaptResult, directionsUrl } = load('api/chat.ts');
const { default: CityResults } = load('components/CityResults.tsx');
const { initialRegion } = load('components/mapRegion.ts');
const direction = (id) => `https://www.google.com/maps/dir/?api=1&destination=${id}%2C+Bengaluru&destination_place_id=${id}`;
function place(id, category = 'accommodation', photo = false) {
  return { id, name: `Test ${id}`, address: `${id} road, Bengaluru`, category, source: 'Google Maps', retrieved_at: '2026-10-25T00:00:00Z',
    location: { latitude: 12.98, longitude: 77.57 }, source_url: 'https://maps.google.com/?cid=123', directions_url: direction(id),
    attributions: [{ provider: 'Test contributor', provider_uri: 'https://example.test/credit' }], image: null,
    photo: photo ? { url: `https://lh3.googleusercontent.com/${id}`, source_url: `https://www.google.com/maps/place/?photo=${id}`,
      authors: [{ name: 'Test photographer', uri: 'https://www.google.com/maps/contrib/123', avatar_url: 'https://lh3.googleusercontent.com/author' }] } : null };
}
function result(places = [place('hotel')]) {
  return adaptResult({ type: 'stay', title: 'Accommodation', description: 'Sourced places', summary: 'Discovery only', details: [], places, status: 'ok', request_id: 'test-request',
    sections: [...new Set(places.map((item) => item.category))].map((category) => ({ intent: category, status: 'ok', description: 'Provider records', summary: 'Listings',
      places: places.filter((item) => item.category === category), limitations: ['Prices, vacancies and immediate check-in have not been confirmed.'], sources: [{ name: 'Google Maps', url: 'https://maps.google.com/' }] })) });
}
async function mount(data, onReply) {
  let tree;
  await act(async () => { tree = create(React.createElement(CityResults, { result: data, onReply })); });
  trees.push(tree);
  return tree;
}
afterEach(async () => {
  await act(async () => trees.forEach((tree) => tree.unmount()));
  trees = []; opened = []; alerts = []; failOpen = false; mapThrows = false;
  constants.executionEnvironment = 'storeClient'; constants.expoConfig.extra = {}; timers.clear();
  mockFetch = async () => { throw Error('Unexpected test network request'); }; shared = [];
});

function civicReport(changes = {}) {
  return { id: 'SN-' + 'a'.repeat(32), category: 'pothole', description: 'Synthetic pothole by entrance', location_text: 'Test road, Indiranagar', latitude: 12.98, longitude: 77.6, location_confirmed: true, public: true, status: 'reported', status_label: 'Reported to Smart Nagrik', created_at: '2026-10-25T10:00:00Z', updated_at: '2026-10-25T10:00:00Z', support_count: 0, supported: false, photo_url: null, is_owner: false, history: [{ status: 'reported', created_at: '2026-10-25T10:00:00Z' }], ...changes };
}
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => ({ success: status < 400, data, error: status < 400 ? null : { message: 'Test save failure' } }) });
async function mountComponent(component, props) {
  let tree;
  await act(async () => { tree = create(React.createElement(component, props)); }); trees.push(tree); return tree;
}
const { ReportForm, PostForm } = load('components/CivicForms.tsx');
const { CivicFeed } = load('components/CivicContent.tsx');
const civicApi = load('api/civic.ts');
const formProps = { styles: {}, photo: null, onPhoto: () => {}, onClose: () => {}, Button: ({ title, onPress }) => React.createElement('Pressable', { accessibilityLabel: title, onPress }, title) };

test('civic draft quick action opens existing workflow without a network submission', async () => {
  const calls = [];
  const data = adaptResult({ type: 'civic', title: 'Review issue', summary: 'Nothing submitted.', description: 'Nothing submitted.', details: [], places: [], quick_replies: [{ id: 'open_report', label: 'Open Report Issue', kind: 'local' }] });
  const tree = await mountComponent(CityResults, { result: data, onCivic: (id) => calls.push(id) });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Open Report Issue' }).props.onPress());
  assert.deepEqual(calls, ['open_report']);
});

test('report form confirms location, saves text-only once and displays actual server reference', async () => {
  const calls = [];
  const saved = civicReport({ latitude: null, longitude: null, location_confirmed: false, public: false });
  mockFetch = async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return reply(saved); };
  const tree = await mountComponent(ReportForm, { ...formProps, draft: { category: 'pothole', description: saved.description, location_text: saved.location_text } });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Submit report' }).props.onPress());
  assert.equal(calls.length, 0);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Confirm issue location' }).props.onPress());
  await act(async () => { const button = tree.root.findByProps({ accessibilityLabel: 'Submit report' }); button.props.onPress(); button.props.onPress(); });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.location_confirmed, false);
  assert.equal(calls[0].body.public, false);
  assert.equal(calls[0].body.latitude, null);
  assert(tree.root.findByProps({ testID: `report-${saved.id}` }));
});

test('failed report submission keeps form and retry ID without claiming success', async () => {
  const bodies = [];
  mockFetch = async (_, options) => { bodies.push(JSON.parse(options.body)); return reply(null, 503); };
  const tree = await mountComponent(ReportForm, { ...formProps, draft: { category: 'pothole', description: 'Another fixture issue', location_text: 'Fixture locality' } });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Confirm issue location' }).props.onPress());
  for (let i = 0; i < 2; i++) await act(async () => tree.root.findByProps({ accessibilityLabel: 'Submit report' }).props.onPress());
  assert.equal(bodies[0].idempotency_key, bodies[1].idempotency_key);
  assert.equal(tree.root.findAllByProps({ testID: `report-${civicReport().id}` }).length, 0);
  assert(JSON.stringify(tree.toJSON()).includes('Test save failure'));
});

test('resolved location requires explicit re-confirmation and changing text removes stale coordinates', async () => {
  const bodies = [];
  mockFetch = async (url, options) => {
    const body = JSON.parse(options.body); bodies.push({ url, body });
    return url.endsWith('resolve-location') ? reply({ status: 'resolved', place: { id: 'fixture', name: 'Fixture', address: 'Bengaluru', location: { latitude: 12.98, longitude: 77.6 }, attributions: [] } }) : reply(civicReport());
  };
  const tree = await mountComponent(ReportForm, { ...formProps, draft: { category: 'pothole', description: 'Fixture issue', location_text: 'Fixture address' } });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Find location' }).props.onPress());
  assert.equal(tree.root.findByProps({ accessibilityLabel: 'Issue latitude' }).props.value, '12.98');
  assert.equal(tree.root.findByProps({ accessibilityLabel: 'Confirm issue location' }).props.accessibilityState.checked, false);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Issue location' }).props.onChangeText('Other address'));
  assert.equal(tree.root.findByProps({ accessibilityLabel: 'Issue latitude' }).props.value, '');
  assert.equal(bodies.length, 1);
});

test('support updates both map detail and matching card, stays idempotent while pending and shares only public text', async () => {
  let calls = 0;
  const report = civicReport();
  mockFetch = async (_, options) => { calls++; assert.equal(JSON.parse(options.body).supported, true); return reply({ ...report, supported: true, support_count: 1 }); };
  const data = adaptResult({ type: 'civic', title: 'Reports', summary: 'Community issue', description: 'Community issue', details: [], places: [], reports: [report] });
  const tree = await mount(data);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Expand map preview' }).props.onPress());
  await act(async () => tree.root.findByType('Marker').props.onPress());
  const buttons = tree.root.findAllByProps({ accessibilityLabel: `With You ${report.id}` });
  assert.equal(buttons.length, 2);
  await act(async () => { buttons[0].props.onPress(); buttons[0].props.onPress(); });
  assert.equal(calls, 1);
  for (const button of tree.root.findAllByProps({ accessibilityLabel: `With You ${report.id}` })) assert.equal(button.props.accessibilityState.selected, true);
  await act(async () => tree.root.findAllByProps({ accessibilityLabel: `Share report ${report.id}` })[0].props.onPress());
  assert(shared[0].message.includes(report.id));
  assert(!shared[0].message.includes('Bearer'));
  assert.equal(tree.root.findAllByType('Text').filter((node) => node.children.join('') === 'Google Maps').length, 0);
});

test('private and unlocated reports cannot become map markers or shareable public content', async () => {
  const report = civicReport({ public: false, latitude: null, longitude: null, location_confirmed: false });
  const data = adaptResult({ type: 'civic', title: 'My report', summary: 'Private', description: 'Private', details: [], places: [], reports: [report], map_markers: [{ ...place('fake'), source: 'Smart Nagrik community' }] });
  assert.equal(data.map_markers.length, 0);
  const tree = await mount(data);
  assert.equal(tree.root.findAllByType('GoogleMap').length, 0);
  assert.equal(tree.root.findAllByProps({ accessibilityLabel: `Share report ${report.id}` }).length, 0);
  assert.equal(civicApi.publicShare(report), null);
});

test('persistent installation credential and retry keys use SecureStore without public JSON token', async () => {
  const first = await civicApi.installationToken(), second = await civicApi.installationToken();
  assert.equal(first, second);
  assert.equal(secureValues.get('smartnagrik.installation.v1'), first);
  assert.match(first, /^[a-f0-9]{64}$/);
  const calls = [];
  mockFetch = async (url, options) => { calls.push(options); return reply({ items: [], next_offset: null }); };
  await civicApi.civicRequest('/v1/civic/my-reports');
  assert.equal(calls[0].headers.Authorization, `Bearer ${first}`);
  assert.equal(calls[0].body, undefined);
  assert.equal(civicApi.mediaPath('https://evil.test/private'), null);
});

test('post form publishes entered text and existing report link only after server success', async () => {
  let body;
  mockFetch = async (_, options) => { body = JSON.parse(options.body); return reply({ id: 'SP-fixture', ...body, created_at: '2026-10-25T00:00:00Z', photo_url: null, report: civicReport() }); };
  const tree = await mountComponent(PostForm, formProps);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Post text' }).props.onChangeText('Fixture community observation'));
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Linked report reference' }).props.onChangeText(civicReport().id));
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Publish post' }).props.onPress());
  assert.equal(body.report_id, civicReport().id);
  assert.equal(body.public, true);
  assert.equal(body.photo_public, false);
  assert(JSON.stringify(tree.toJSON()).includes('SP-fixture'));
});

test('profile/feed loads actual records with bounded pagination and reload support state', async () => {
  const urls = [];
  mockFetch = async (url) => { urls.push(url); return reply({ items: [civicReport({ supported: true, support_count: 1 })], next_offset: null }); };
  const tree = await mountComponent(CivicFeed, { mine: true });
  assert(urls[0].includes('/v1/civic/my-reports?limit=10&offset=0'));
  assert.equal(tree.root.findByProps({ accessibilityLabel: `With You ${civicReport().id}` }).props.accessibilityState.selected, true);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Refresh saved records' }).props.onPress());
  assert.equal(urls.length, 2);
});

test('itinerary cards preserve source photos, numbered markers and real stop directions', async () => {
  const stop = { ...place('museum', 'tourism', true), itinerary_order: 1, visit_minutes: 60, planning_reason: 'Returned for your heritage interests', itinerary_kind: 'attraction' };
  const tree = await mount(result([stop]));
  assert(JSON.stringify(tree.toJSON()).includes('planning estimate'));
  assert.equal(tree.root.findByType('Marker').findAllByType('Text')[0].children.join(''), '1');
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Directions to Test museum' }).props.onPress());
  assert.equal(opened[0], direction('museum'));
});

test('photo submission sends selected URI and consent as multipart while preserving retry identity', async () => {
  let sent;
  mockFetch = async (_, options) => { sent = options; return reply(civicReport({ photo_url: '/v1/civic/media/' + 'b'.repeat(32) })); };
  const photo = { uri: 'file:///test-photo.jpg', name: 'test-photo.jpg', type: 'image/jpeg' };
  const tree = await mountComponent(ReportForm, { ...formProps, photo, draft: { category: 'pothole', description: 'Fixture photo report', location_text: 'Fixture road' } });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Confirm issue location' }).props.onPress());
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Publish report to community' }).props.onPress());
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Publish report photo' }).props.onPress());
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Submit report' }).props.onPress());
  assert(sent.body instanceof NativeFormData);
  assert.equal(sent.headers['Content-Type'], undefined); // RN supplies boundary.
  assert.equal(sent.body.parts.find(([key]) => key === 'photo')[1], photo);
  const payload = JSON.parse(sent.body.parts.find(([key]) => key === 'payload')[1]);
  assert.equal(payload.photo_public, true);
  assert.match(payload.idempotency_key, /^[a-f0-9-]{36}$/);
  const image = tree.root.findByType('NativeImage');
  assert.equal(image.props.source.uri, 'http://localhost:5000/v1/civic/media/' + 'b'.repeat(32));
  assert(image.props.source.headers.Authorization.startsWith('Bearer '));
  await act(async () => image.props.onError());
  assert(tree.root.findByProps({ testID: `report-${civicReport().id}` }));
  assert(JSON.stringify(tree.toJSON()).includes('Photo unavailable'));
});

test('view report refreshes stored status and failed support leaves the previous count', async () => {
  const report = civicReport();
  const { CivicReportCard } = load('components/CivicContent.tsx');
  mockFetch = async (url) => url.endsWith('/support') ? reply(null, 503) : reply({ ...report, support_count: 2 });
  const tree = await mountComponent(CivicReportCard, { report });
  await act(async () => tree.root.findByProps({ accessibilityLabel: `View report ${report.id}` }).props.onPress());
  assert(JSON.stringify(tree.toJSON()).includes('not an official government complaint'));
  await act(async () => tree.root.findByProps({ accessibilityLabel: `With You ${report.id}` }).props.onPress());
  assert.equal(alerts.length, 1);
  const text = tree.root.findByProps({ accessibilityLabel: `With You ${report.id}` }).findByType('Text').children.join('');
  assert(text.endsWith('2'));
});

test('actual App chat draft opens the existing report sheet with supplied details and no automatic save', async () => {
  const { default: App } = load('App.tsx');
  const requests = [];
  mockFetch = async (url) => { requests.push(url); return reply({ type: 'civic', title: 'Review issue', description: 'Nothing submitted.', summary: 'Nothing submitted.', details: [], places: [], report_draft: { category: 'pothole', description: 'Pothole outside college', location_text: 'Test locality' }, quick_replies: [{ id: 'open_report', label: 'Open Report Issue', kind: 'local' }] }); };
  const tree = await mountComponent(App, {});
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Ask Smart Nagrik a question' }).props.onChangeText('Pothole outside college'));
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Send request' }).props.onPress());
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Open Report Issue' }).props.onPress());
  assert.equal(tree.root.findByProps({ accessibilityLabel: 'Issue description' }).props.value, 'Pothole outside college');
  assert.equal(tree.root.findByProps({ accessibilityLabel: 'Issue location' }).props.value, 'Test locality');
  assert.equal(requests.length, 1);
  assert(requests[0].endsWith('/v1/chat'));
  assert.equal(tree.root.findByType('Modal').props.visible, true);
});

test('actual Profile My reports control loads installation history inside the existing sheet', async () => {
  const { default: App } = load('App.tsx');
  const urls = [];
  mockFetch = async (url) => { urls.push(url); return reply({ items: [civicReport()], next_offset: null }); };
  const tree = await mountComponent(App, {});
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Open profile' }).props.onPress());
  const history = tree.root.findAllByType('Pressable').find((node) => node.findAllByType('Text').some((text) => text.children.join('') === 'My reports'));
  await act(async () => history.props.onPress());
  assert(urls[0].includes('/v1/civic/my-reports'));
  assert.equal(tree.root.findAllByProps({ testID: `report-${civicReport().id}` }).length, 1);
  assert.equal(tree.root.findByType('Modal').props.visible, true);
});

test('place cards display actual records with readable attribution and matched Directions', async () => {
  const tree = await mount(result());
  const texts = tree.root.findAllByType('Text').map((node) => node.children.join(''));
  assert(texts.includes('Test hotel'));
  assert(texts.includes('hotel road, Bengaluru'));
  assert(texts.includes('Google Maps'));
  const credit = tree.root.findAllByType('Text').find((node) => node.children.join('') === 'Google Maps');
  assert.equal(credit.props.style.color, '#5E5E5E');
  assert.equal(credit.props.style.fontSize, 12);
  assert.equal(credit.props.numberOfLines, undefined);
  const button = tree.root.findAllByType('Pressable').find((node) => node.props.accessibilityLabel === 'Directions to Test hotel');
  await act(async () => { button.props.onPress(); });
  assert.equal(opened[0], direction('hotel'));
});

test('Directions failure alerts without removing successful cards', async () => {
  const tree = await mount(result());
  failOpen = true;
  await act(async () => tree.root.findAllByType('Pressable').find((node) => node.props.accessibilityLabel === 'Directions to Test hotel').props.onPress());
  assert.equal(alerts.length, 1);
  assert.equal(tree.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
});

test('invalid URLs and destination IDs never produce executable Directions', async () => {
  for (const url of ['javascript:alert(1)', 'https://evil.test/maps/dir/?api=1&destination=x', 'https://www.google.com/maps/dir/?api=1', direction('different')]) {
    assert.equal(directionsUrl(url, 'hotel'), null);
  }
  const bad = place('hotel'); bad.directions_url = direction('different');
  const tree = await mount(result([bad]));
  assert(!tree.root.findAllByType('Pressable').some((node) => node.props.accessibilityLabel.startsWith('Directions')));
});

test('mixed result preserves hotel/food section order and category ownership', async () => {
  const tree = await mount(result([place('hotel'), place('food', 'food_places')]));
  const sections = tree.root.findAllByType('View').filter((node) => node.props.testID?.startsWith('section-'));
  assert.deepEqual(sections.map((node) => node.props.testID), ['section-accommodation', 'section-food_places']);
  assert.equal(sections[0].findAllByProps({ testID: 'place-hotel' }).length, 1);
  assert.equal(sections[0].findAllByProps({ testID: 'place-food' }).length, 0);
  assert(JSON.stringify(tree.toJSON()).includes('have not been confirmed'));
});

test('photo uses matched URI, no disk cache, credits and individual source link', async () => {
  const tree = await mount(result([place('hotel', 'accommodation', true)]));
  const image = tree.root.findAllByType('ProviderImage').find((node) => node.props.accessibilityLabel === 'Photo of Test hotel');
  assert.equal(image.props.source, 'https://lh3.googleusercontent.com/hotel');
  assert.equal(image.props.cachePolicy, 'none');
  assert.equal(image.props.style.height, 155);
  assert(JSON.stringify(tree.toJSON()).includes('Test photographer'));
  const source = tree.root.findAllByType('Pressable').find((node) => node.props.accessibilityLabel === 'View original photo of Test hotel');
  await act(async () => source.props.onPress());
  assert.equal(opened[0], 'https://www.google.com/maps/place/?photo=hotel');
  await act(async () => image.props.onError());
  assert(JSON.stringify(tree.toJSON()).includes('Photo unavailable'));
  assert.equal(tree.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
  assert(tree.root.findAllByType('Pressable').some((node) => node.props.accessibilityLabel === 'Directions to Test hotel'));
});

test('malformed photo or absent photo does not hide valid places or invent an image', async () => {
  const item = place('hotel', 'accommodation', true); item.photo.url = 'https://evil.test/image.jpg';
  const data = result([item]);
  assert.equal(data.places[0].image, null);
  const tree = await mount(data);
  assert.equal(tree.root.findAllByType('ProviderImage').length, 0);
  assert.equal(tree.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
});

test('map uses returned coordinates, Google provider and no scroll/GPS gestures', async () => {
  const data = result([place('hotel'), place('food', 'food_places')]);
  const tree = await mount(data);
  const map = tree.root.findByType('GoogleMap');
  assert.equal(map.props.provider, 'google');
  assert.equal(map.props.initialRegion.latitude, 12.98);
  for (const field of ['scrollEnabled', 'zoomEnabled', 'pitchEnabled', 'rotateEnabled', 'showsUserLocation', 'showsMyLocationButton']) assert.equal(map.props[field], false);
  assert.equal(tree.root.findAllByType('Marker').length, 2);
  const initial = map.props.initialRegion;
  await act(async () => { map.props.onMapLoaded(); tree.update(React.createElement(CityResults, { result: data })); });
  assert.equal(tree.root.findByType('GoogleMap').props.initialRegion, initial);
  assert.equal(timers.size, 0);
});

test('map absent coordinates, missing custom build key, and load timeout preserve cards', async () => {
  const item = place('hotel'); item.location = null;
  const noCoordinates = await mount(result([item]));
  assert.equal(noCoordinates.root.findAllByType('GoogleMap').length, 0);
  constants.executionEnvironment = 'standalone';
  const unconfigured = await mount(result());
  assert.equal(unconfigured.root.findAllByType('GoogleMap').length, 0);
  assert(JSON.stringify(unconfigured.toJSON()).includes('Map unavailable'));
  constants.executionEnvironment = 'storeClient';
  const timeout = await mount(result());
  await act(async () => [...timers.values()].forEach((fn) => fn()));
  assert.equal(timeout.root.findAllByType('GoogleMap').length, 0);
  assert.equal(timeout.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
});

test('missing native map module is isolated by error boundary', async () => {
  mapThrows = true;
  const original = console.error; console.error = () => {};
  let tree;
  try { tree = await mount(result()); } finally { console.error = original; }
  assert(JSON.stringify(tree.toJSON()).includes('Map unavailable'));
  assert.equal(tree.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
});

test('weather-only result has no extra content, maps or images', async () => {
  const tree = await mount(adaptResult({ type: 'weather', title: 'Forecast', description: 'Open-Meteo forecast', details: [], places: [], sections: [{ intent: 'weather_conditions', status: 'ok', description: 'Forecast', places: [] }] }));
  assert.equal(tree.toJSON(), null);
});

test('transport opens supplied transit URL without inventing origin', async () => {
  const url = 'https://www.google.com/maps/dir/?api=1&destination=Majestic%2C+Bengaluru&travelmode=transit';
  const tree = await mount(adaptResult({ type: 'guide', title: 'Journey', description: 'Unknown origin', details: [], places: [], sections: [{ intent: 'transport', status: 'handoff_available', description: 'Unknown origin', places: [], directions_url: url }] }));
  await act(async () => tree.root.findAllByType('Pressable').find((node) => node.props.accessibilityLabel === 'Open journey in Google Maps').props.onPress());
  assert.equal(opened[0], url);
  assert.equal(tree.root.findAllByType('GoogleMap').length, 0);
});

test('partial success keeps successful section and explains failed secondary', async () => {
  const data = result();
  data.sections.push({ intent: 'food_places', status: 'timeout', description: 'The city provider timed out.', places: [] });
  const tree = await mount(data);
  assert(JSON.stringify(tree.toJSON()).includes('timed out'));
  assert.equal(tree.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
});

test('adapter bounds cards and rejects category mixing and invalid coordinates', () => {
  const items = Array.from({ length: 10 }, (_, i) => place(`id${i}`));
  items[0].location = { latitude: 28, longitude: 77 };
  const data = result(items);
  assert.equal(data.places.length, 6);
  assert.equal(data.sections[0].places.length, 6);
  assert.equal(data.places[0].location, null);
  assert.equal(initialRegion([{ ...items[0], location: null }]), null);
  assert.equal(adaptResult({ ...data, type: 'invented' }), null);
});

test('same provider place can belong to two requested sections without losing either need', () => {
  const data = result([place('same'), place('same', 'food_places')]);
  assert.equal(data.sections[0].places[0].category, 'accommodation');
  assert.equal(data.sections[1].places[0].category, 'food_places');
  assert.equal(data.sections[0].places[0].id, data.sections[1].places[0].id);
});

test('App.tsx hero, styles, city picker, emergency and native/messaging configuration preserve pre-integration baseline', () => {
  const baseline = JSON.parse(fs.readFileSync(path.join(root, '../backend/evaluation/prewhatsapp_preservation.json'), 'utf8'));
  const app = fs.readFileSync(path.join(root, 'App.tsx'), 'utf8').replace(/\r\n/g, '\n');
  const digest = (value) => crypto.createHash('sha256').update(value).digest('hex');
  const chunks = { styles: app.slice(app.indexOf('const styles = StyleSheet.create(')), hero: app.slice(app.indexOf('          <View style={styles.heroSection}>'), app.indexOf('          <View style={styles.resultsSection}>')), city_picker: app.slice(app.indexOf('function CitySheet('), app.indexOf('function ProfileSheet(')), emergency: app.slice(app.indexOf('function EmergencySheet('), app.indexOf('function AlertsSheet(')) };
  for (const [name, content] of Object.entries(chunks)) assert.equal(digest(content), baseline.app_chunks[name], name);
  for (const [file, hash] of Object.entries(baseline.files)) {
    assert.equal(digest(fs.readFileSync(path.join(root, '..', file))), hash, file);
  }
});

test('custom map configuration requires separate mobile key and preserves app identity', () => {
  const configure = require('../app.config.js');
  const base = require('../app.json').expo;
  const saved = process.env.GOOGLE_MAPS_ANDROID_API_KEY;
  try {
    delete process.env.GOOGLE_MAPS_ANDROID_API_KEY;
    let config = configure({ config: base });
    assert.equal(config.extra.androidMapsConfigured, false);
    assert.equal(config.android.package, 'com.smartnagrik.app');
    assert.deepEqual(config.android, base.android);
    assert(!config.plugins.some((plugin) => Array.isArray(plugin) && plugin[0] === 'react-native-maps'));
    process.env.GOOGLE_MAPS_ANDROID_API_KEY = 'synthetic-mobile-test-key';
    config = configure({ config: base });
    assert.equal(config.extra.androidMapsConfigured, true);
    assert.equal(config.plugins.find((plugin) => Array.isArray(plugin) && plugin[0] === 'react-native-maps')[1].androidGoogleMapsApiKey, 'synthetic-mobile-test-key');
    assert(!JSON.stringify(config.extra).includes('synthetic-mobile-test-key'));
    assert.deepEqual(config.android, base.android);
  } finally {
    if (saved === undefined) delete process.env.GOOGLE_MAPS_ANDROID_API_KEY;
    else process.env.GOOGLE_MAPS_ANDROID_API_KEY = saved;
  }
});

test('ordinary re-render preserves photo instance/source and author profile action', async () => {
  const data = result([place('hotel', 'accommodation', true)]);
  const tree = await mount(data);
  const image = tree.root.findAllByType('ProviderImage').find((node) => node.props.accessibilityLabel === 'Photo of Test hotel');
  await act(async () => tree.update(React.createElement(CityResults, { result: data })));
  const sameImage = tree.root.findAllByType('ProviderImage').find((node) => node.props.accessibilityLabel === 'Photo of Test hotel');
  assert.equal(image, sameImage);
  assert.equal(image.props.source, sameImage.props.source);
  const profile = tree.root.findAllByType('Pressable').find((node) => node.props.accessibilityLabel === "View Test photographer's profile");
  await act(async () => profile.props.onPress());
  assert.equal(opened[0], 'https://www.google.com/maps/contrib/123');
});

test('existing map expands in place, preserves instance/camera, selects matching card and collapses on Android back', async () => {
  const tree = await mount(result());
  const before = tree.root.findByType('GoogleMap');
  const region = before.props.initialRegion;
  assert.equal(before.props.scrollEnabled, false);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Expand map preview' }).props.onPress());
  const expanded = tree.root.findByType('GoogleMap');
  assert.equal(expanded, before);
  assert.equal(expanded.props.initialRegion, region);
  assert.equal(expanded.props.scrollEnabled, true);
  assert.equal(expanded.props.zoomEnabled, true);
  assert.equal(expanded.props.style[1].height, 400);
  await act(async () => tree.root.findByType('Marker').props.onPress());
  assert.equal(tree.root.findByProps({ testID: 'place-hotel' }).props.style[1].borderWidth, 2);
  assert.equal(tree.root.findByType('GoogleMap').props.initialRegion, region);
  assert.equal(tree.root.findAllByType('Polyline').length, 0);
  await act(async () => assert.equal(backHandler(), true));
  assert.equal(tree.root.findByType('GoogleMap'), before);
  assert.equal(tree.root.findByType('GoogleMap').props.scrollEnabled, false);
  assert.equal(backHandler, null);
  assert.equal(tree.root.findAllByType('Modal').length, 0);
});

test('map toggle collapses without losing result cards or requesting new data', async () => {
  let requests = 0;
  const tree = await mount(result(), async () => { requests++; });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Explore map' }).props.onPress());
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Collapse map' }).props.onPress());
  assert.equal(tree.root.findAllByProps({ testID: 'place-hotel' }).length, 1);
  assert.equal(requests, 0);
});

test('typed transport endpoints retain correct coordinates and journey Directions', async () => {
  const origin = { ...place('o'), marker_type: 'origin', category: 'transport' };
  const destination = { ...place('d'), marker_type: 'destination', category: 'transport', location: { latitude: 13.08, longitude: 77.65 } };
  const url = direction('d') + '&origin=o&travelmode=walking';
  destination.directions_url = url;
  const data = adaptResult({ type: 'guide', title: 'Walking', description: 'Endpoints only', summary: 'Explore directions', details: [], places: [], map_markers: [origin, destination], sections: [{ intent: 'transport', description: 'Endpoints only', status: 'handoff_available', places: [], directions_url: url }] });
  const tree = await mount(data);
  const markers = tree.root.findAllByType('Marker');
  assert.equal(markers.length, 2);
  assert.equal(markers[1].props.coordinate.latitude, 13.08);
  assert.notEqual(markers[0].props.pinColor, markers[1].props.pinColor);
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Open journey in Google Maps' }).props.onPress());
  assert.equal(opened[0], url);
});

test('weather hourly tile reveals existing forecast with no map or provider call', async () => {
  const data = adaptResult({ type: 'weather', title: 'Weather', summary: 'Carry an umbrella.', description: 'Technical forecast source', details: [], places: [], quick_replies: [{ id: 'show_details', label: 'Hourly forecast', kind: 'local' }], sections: [{ intent: 'weather_conditions', description: 'Forecast', places: [], weather: { hourly: [{ time: '2026-10-25T18:00:00+05:30', precipitation_interval_end: '2026-10-25T19:00:00+05:30', temperature_2m: 24, precipitation_probability: 70 }] } }] });
  let requests = 0;
  const tree = await mount(data, async () => { requests++; });
  assert(!JSON.stringify(tree.toJSON()).includes('rain chance 70%'));
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Hourly forecast' }).props.onPress());
  assert(JSON.stringify(tree.toJSON()).includes('rain chance '));
  assert.equal(tree.root.findAllByType('GoogleMap').length, 0);
  assert.equal(requests, 0);
});

test('quick reply sends structured context once despite repeated taps', async () => {
  const data = result();
  data.quick_replies = [{ id: 'food_nearby', label: 'Food nearby', kind: 'request' }];
  const calls = [];
  let finish;
  const tree = await mount(data, (...args) => { calls.push(args); return new Promise((resolve) => { finish = resolve; }); });
  const button = tree.root.findByProps({ accessibilityLabel: 'Food nearby' });
  await act(async () => { button.props.onPress(); button.props.onPress(); });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].follow_up, true);
  assert.equal(calls[0][1].quick_reply, 'food_nearby');
  await act(async () => finish());
});

test('change-area tile collects input and submits a contextual refinement', async () => {
  const data = result();
  data.quick_replies = [{ id: 'change_area', label: 'Change area', kind: 'input' }];
  const calls = [];
  const tree = await mount(data, async (...args) => { calls.push(args); });
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Change area' }).props.onPress());
  await act(async () => tree.root.findByType('TextInput').props.onChangeText('HSR Layout'));
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Apply change' }).props.onPress());
  assert.equal(calls[0][0], 'Change area to HSR Layout');
  assert.equal(calls[0][1].follow_up, true);
});

test('adapter rejects unknown actions, mismatched kinds and unlabelled demo data', () => {
  const data = result();
  data.quick_replies = [{ id: 'execute', label: 'Bad', kind: 'request' }, { id: 'mode_walk', label: 'Bad local', kind: 'local' }];
  data.sections[0].demo = { static_label: 'Fake live bus', notice: 'Live', stops: ['x'], source: 'https://example.test' };
  const cleaned = adaptResult(data);
  assert.equal(cleaned.quick_replies.length, 0);
  assert.equal(cleaned.sections[0].demo, null);
});

test('session adapter sends latest text with session ID and resets context on city change', async () => {
  const exports = {};
  const bodies = [];
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'api/chat.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const id = 'synthetic-session-token-123456';
  vm.runInNewContext(code, { exports, require: () => ({ identityHeaders: async () => ({ Authorization: 'Bearer synthetic-installation-token-123456' }) }), URL, AbortController, setTimeout, clearTimeout, process: { env: { EXPO_PUBLIC_API_URL: 'http://localhost:5000' } },
    fetch: async (url, options) => { bodies.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ success: true, data: { ...result(), session_id: id } }) }; } });
  await exports.sendChat('From HSR Layout to Whitefield', 'Bengaluru');
  await exports.sendChat('Walk', 'Bengaluru', { follow_up: true, quick_reply: 'mode_walk' });
  assert.deepEqual(bodies[0], { message: 'From HSR Layout to Whitefield', city: 'Bengaluru' });
  assert.deepEqual(bodies[1], { message: 'Walk', city: 'Bengaluru', session_id: id, follow_up: true, quick_reply: 'mode_walk' });
  await exports.sendChat('Weather', 'Another city');
  assert.equal(bodies[2].session_id, undefined);
});

test('configured transport demo displays simulation notice and source', async () => {
  const data = adaptResult({ type: 'guide', title: 'Metro', summary: 'Explore transport', description: 'Endpoints only', details: [], places: [], sections: [{ intent: 'transport', description: 'Metro example', places: [], demo: { notice: 'Demo / Simulated: example frequency 5 minutes; not live.', static_label: 'Purple Line static station sequence', stops: ['Majestic', 'MG Road'], source: 'https://www.bmrc.co.in/schematic-route-map/' } }] });
  const tree = await mount(data);
  assert(JSON.stringify(tree.toJSON()).includes('Demo / Simulated: example frequency 5 minutes; not live.'));
  await act(async () => tree.root.findByProps({ accessibilityLabel: 'Static transport source' }).props.onPress());
  assert.equal(opened[0], 'https://www.bmrc.co.in/schematic-route-map/');
});

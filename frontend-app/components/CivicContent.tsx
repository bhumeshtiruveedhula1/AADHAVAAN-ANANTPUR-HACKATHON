import { useEffect, useRef, useState } from 'react';
import { Alert, Image, Pressable, Share, StyleSheet, Text, View } from 'react-native';
import { civicRequest, type CivicReport, type CommunityPost, type Page, mediaSource, onReportChange, publicShare, publishReport, setSupport } from '../api/civic';

export function CivicPhoto({ path }: { path: string | null }) {
  return path ? <LoadedPhoto key={path} path={path} /> : null;
}

function LoadedPhoto({ path }: { path: string }) {
  const [source, setSource] = useState<Awaited<ReturnType<typeof mediaSource>> | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { let active = true; void mediaSource(path).then((value) => { if (active) setSource(value); }).catch(() => { if (active) setFailed(true); }); return () => { active = false; }; }, [path]);
  return failed ? <Text style={styles.copy}>Photo unavailable</Text> : source ? <Image source={source} style={styles.photo} accessibilityLabel="User-submitted issue or post photo" onError={() => setFailed(true)} /> : <Text style={styles.copy}>Loading photo…</Text>;
}

export function CivicReportCard({ report: initial, selected = false }: { report: CivicReport; selected?: boolean }) {
  const [report, setReport] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const locked = useRef(false);
  const [previous, setPrevious] = useState(initial);
  if (previous !== initial) { setPrevious(initial); setReport(initial); }
  useEffect(() => onReportChange((value) => { if (value.id === initial.id) setReport(value); }), [initial.id]);
  const support = async () => {
    if (locked.current) return;
    locked.current = true; setBusy(true);
    try { await setSupport(report.id, !report.supported); }
    catch (error) { Alert.alert('Could not update support', error instanceof Error ? error.message : 'Please try again.'); }
    finally { locked.current = false; setBusy(false); }
  };
  const view = async () => {
    try { const latest = await civicRequest<CivicReport>(`/v1/civic/reports/${encodeURIComponent(report.id)}`); publishReport(latest); setExpanded((value) => !value); }
    catch (error) { Alert.alert('Report unavailable', error instanceof Error ? error.message : 'Please try again.'); }
  };
  return <View testID={`report-${report.id}`} style={[styles.card, selected && styles.selected]}>
    <CivicPhoto path={report.photo_url} />
    <View style={styles.content}><Text style={styles.source}>COMMUNITY REPORT · {report.category.replace(/_/g, ' ')}</Text>
      <Text style={styles.title}>{report.description}</Text><Text style={styles.copy}>{report.location_text}</Text>
      <Text style={styles.copy}>{report.status_label} · {report.id}</Text><Text style={styles.copy}>{new Date(report.created_at).toLocaleString()}</Text>
      <View style={styles.actions}>
        <Pressable accessibilityRole="button" accessibilityLabel={`View report ${report.id}`} style={styles.button} onPress={() => void view()}><Text style={styles.buttonText}>{expanded ? 'Hide report details' : 'View report'}</Text></Pressable>
        {report.public && <Pressable accessibilityRole="button" accessibilityLabel={`With You ${report.id}`} accessibilityState={{ selected: report.supported, disabled: busy }} disabled={busy} style={[styles.button, report.supported && styles.selected]} onPress={() => void support()}><Text style={styles.buttonText}>💪 {report.supported ? 'With You ✓' : 'With You'} · {report.support_count}</Text></Pressable>}
        {report.public && <Pressable accessibilityRole="button" accessibilityLabel={`Share report ${report.id}`} style={styles.button} onPress={() => { const message = publicShare(report); if (message) void Share.share({ message }).catch(() => Alert.alert('Could not share', 'Please try again.')); }}><Text style={styles.buttonText}>Share</Text></Pressable>}
      </View>
      {expanded && <View><Text style={styles.copy}>Saved in Smart Nagrik. This is not an official government complaint or verification. Support means this concern matters to another installation.</Text>
        {report.history?.map((item, index) => <Text style={styles.copy} key={`${item.created_at}-${index}`}>{item.status} · {new Date(item.created_at).toLocaleString()}</Text>)}
      </View>}
    </View>
  </View>;
}

export function CivicFeed({ mine = false, posts = false }: { mine?: boolean; posts?: boolean }) {
  const [items, setItems] = useState<(CivicReport | CommunityPost)[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const endpoint = posts ? '/v1/community/posts' : mine ? '/v1/civic/my-reports' : '/v1/civic/reports';
  const suffix = posts && mine ? '&mine=true' : '';
  const load = async (offset = 0) => {
    if (lock.current) return; lock.current = true; setBusy(true); setError('');
    try {
      const data = await civicRequest<Page<CivicReport | CommunityPost>>(`${endpoint}?limit=10&offset=${offset}${suffix}`);
      setItems((old) => offset ? [...old, ...data.items] : data.items); setNext(data.next_offset);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to load records.'); }
    finally { lock.current = false; setBusy(false); }
  };
  useEffect(() => {
    let active = true;
    void civicRequest<Page<CivicReport | CommunityPost>>(`${endpoint}?limit=10&offset=0${suffix}`)
      .then((data) => { if (active) { setItems(data.items); setNext(data.next_offset); } })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'Unable to load records.'); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [endpoint, suffix]);
  return <View><Text style={styles.title}>{mine ? (posts ? 'My posts' : 'My reports') : posts ? 'Community posts' : 'Community reports'}</Text>
    <Text style={styles.copy}>Saved community information, not official news or verified warnings.</Text>
    {busy && <Text style={styles.copy}>Loading…</Text>}{error ? <Text accessibilityRole="alert" style={styles.copy}>{error}</Text> : null}
    {!busy && !error && !items.length && <Text style={styles.copy}>No saved {posts ? 'posts' : 'reports'} found.</Text>}
    {items.map((item) => 'category' in item ? <CivicReportCard key={item.id} report={item} /> : <View key={item.id} style={styles.card}>
      <CivicPhoto path={item.photo_url} /><View style={styles.content}><Text style={styles.source}>COMMUNITY POST</Text><Text style={styles.title}>{item.text}</Text><Text style={styles.copy}>{item.location_text}</Text><Text style={styles.copy}>{new Date(item.created_at).toLocaleString()}</Text>
        {item.public && <Pressable accessibilityLabel={`Share post ${item.id}`} style={styles.button} onPress={() => void Share.share({ message: `Community post: ${item.text}\n${item.location_text || ''}\nSmart Nagrik reference ${item.id}` }).catch(() => Alert.alert('Could not share', 'Please try again.'))}><Text style={styles.buttonText}>Share</Text></Pressable>}
      </View>{item.report && <CivicReportCard report={item.report} />}</View>)}
    <View style={styles.actions}><Pressable accessibilityRole="button" accessibilityLabel="Refresh saved records" disabled={busy} style={styles.button} onPress={() => void load()}><Text style={styles.buttonText}>Refresh</Text></Pressable>
      {next !== null && <Pressable accessibilityRole="button" disabled={busy} style={styles.button} onPress={() => void load(next)}><Text style={styles.buttonText}>Load more</Text></Pressable>}</View>
  </View>;
}

// Existing Manus feed/card/button tokens, scoped to data-backed content.
const styles = StyleSheet.create({
  card: { backgroundColor: '#FFF', borderRadius: 18, overflow: 'hidden', borderWidth: 1, borderColor: 'rgba(20,24,29,0.13)', marginBottom: 14 },
  content: { padding: 14, gap: 6 }, photo: { width: '100%', height: 170 },
  title: { color: '#14181D', fontFamily: 'Manrope', fontSize: 15, fontWeight: '800', marginBottom: 5 },
  copy: { color: '#73736F', fontFamily: 'Manrope', fontSize: 12, lineHeight: 18 }, source: { color: '#14181D', fontFamily: 'DMMono', fontSize: 10 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 12 }, button: { minHeight: 44, justifyContent: 'center', borderWidth: 1, borderColor: 'rgba(20,24,29,0.13)', borderRadius: 9, paddingHorizontal: 8, paddingVertical: 7 },
  buttonText: { color: '#14181D', fontFamily: 'Manrope', fontSize: 12 }, selected: { borderColor: '#E77B4B', backgroundColor: '#F9E3D8' },
});

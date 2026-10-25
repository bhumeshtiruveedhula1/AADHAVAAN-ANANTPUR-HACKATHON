import { memo, useRef, useState } from 'react';
import { Alert, Linking, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Image } from 'expo-image';
import { type ChatOptions, type ChatResult, type PlaceRecord, type ResultSection, directionsUrl, httpsUrl } from '../api/chat';
import InlinePlacesMap from './InlinePlacesMap';
import { CivicReportCard } from './CivicContent';

const labels: Record<string, string> = { accommodation: 'Accommodation', food_places: 'Food', places_services: 'City places', tourism: 'Tourist attractions', transport: 'Transport', weather_conditions: 'Weather' };

async function openLink(url: string) {
  try { await Linking.openURL(url); }
  catch { Alert.alert('Could not open link', 'Please check that a browser or Google Maps is available and try again.'); }
}

function LinkButton({ url, label, title, primary = false }: { url: string | null | undefined; label: string; title?: string; primary?: boolean }) {
  const safe = httpsUrl(url);
  if (!safe) return null;
  return <Pressable accessibilityRole="link" accessibilityLabel={label} onPress={() => void openLink(safe)} style={primary ? styles.actionPrimary : styles.actionSecondary}>
    <Text style={primary ? styles.actionPrimaryText : styles.actionText}>{title || label}</Text>
  </Pressable>;
}

function GoogleAttribution() {
  return <Text style={styles.googleAttribution}>Google Maps</Text>;
}

function AuthorAvatar({ uri }: { uri: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? null : <Image source={uri} cachePolicy="none" style={styles.avatar} onError={() => setFailed(true)} accessibilityLabel="Photo author avatar" />;
}

const PlaceCard = memo(function PlaceCard({ place, selected }: { place: PlaceRecord; selected?: boolean }) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const photo = place.photo;
  const directions = directionsUrl(place.directions_url, place.id);
  return <View style={[styles.placeCard, selected && styles.selected]} testID={`place-${place.id}`}>
    {photo && <View style={styles.placeImage}>
      {failedImage === photo.url
        ? <View style={styles.imageFallback}><Text style={styles.body}>Photo unavailable</Text></View>
        : <Image source={photo.url} cachePolicy="none" contentFit="cover" style={styles.placeImage} accessibilityLabel={`Photo of ${place.name}`} onError={() => setFailedImage(photo.url)} />}
    </View>}
    <View style={styles.placeInfo}>
      <Text style={styles.placeTitle}>{place.name}</Text>
      {place.itinerary_order && <Text style={styles.category}>Stop {place.itinerary_order} · Suggested visit {place.visit_minutes} min (planning estimate)</Text>}
      {place.planning_reason && <Text style={styles.body}>{place.planning_reason}</Text>}
      <Text style={styles.body}>{place.address}</Text>
      <Text style={styles.category}>{place.itinerary_kind === 'food' ? 'Food stop' : labels[place.category] || 'Place'}</Text>
      <View style={styles.actions}>
        <LinkButton url={directions} label={`Directions to ${place.name}`} title="Directions" primary />
        <LinkButton url={place.source_url} label={`View ${place.name} on Google Maps`} title="View on Google Maps" />
      </View>
      <GoogleAttribution />
      {place.attributions.map((credit, index) => <View key={`${credit.provider}-${index}`}>
        <Text style={styles.credit}>{credit.provider}</Text>
        <LinkButton url={credit.provider_uri} label={`Source: ${credit.provider}`} />
      </View>)}
      {photo && <View style={styles.credits}>
        {photo.authors.map((author, index) => <View key={`${author.name}-${index}`} style={styles.author}>
          {author.avatar_url && <AuthorAvatar uri={author.avatar_url} />}
          <View style={styles.authorText}><Text style={styles.credit}>Photo: {author.name}</Text><LinkButton url={author.uri} label={`View ${author.name}'s profile`} title="Author profile" /></View>
        </View>)}
        <LinkButton url={photo.source_url} label={`View original photo of ${place.name}`} title="View original photo" />
      </View>}
    </View>
  </View>;
});

export function resultSections(result: ChatResult): ResultSection[] {
  if (result.sections?.length) return result.sections;
  const categories = [...new Set(result.places.map((place) => place.category))];
  return categories.map((category) => ({ intent: category, status: result.status || 'ok', description: '', places: result.places.filter((place) => place.category === category) }));
}

export default function CityResults({ result, onReply, onCivic }: { result: ChatResult; onReply?: (message: string, options?: ChatOptions) => Promise<void>; onCivic?: (action: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const [edit, setEdit] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const sections = resultSections(result);
  const weatherOnly = sections.length === 1 && sections[0].intent === 'weather_conditions';
  if (!result.summary && !sections.some((section) => section.places.length || section.intent === 'transport')) return null;
  const markers = [...(result.map_markers || []), ...sections.flatMap((section) => section.places)].filter((place, index, all) => all.findIndex((other) => other.id === place.id) === index).slice(0, 6);
  const send = async (message: string, quick?: string) => {
    if (!onReply || locked.current) return;
    locked.current = true; setBusy(true);
    try { await onReply(message, { follow_up: true, ...(quick ? { quick_reply: quick } : {}) }); }
    finally { locked.current = false; setBusy(false); }
  };
  return <View style={styles.placeList}>
    {sections.filter(() => !weatherOnly).map((section, index) => <View key={`${section.intent}-${index}`} style={styles.section} testID={`section-${section.intent}`}>
      <Text style={styles.placeHeading}>{labels[section.intent] || section.intent.replace(/_/g, ' ')}</Text>
      <Text style={styles.body}>{section.summary || section.description}</Text>
      {section.demo && <View><Text style={styles.body}>{section.demo.static_label}</Text><Text style={styles.body}>{section.demo.stops.join(' → ')}</Text><Text style={styles.credit}>{section.demo.notice}</Text><LinkButton url={section.demo.source} label="Static transport source" /></View>}
      {section.places.map((place) => <PlaceCard key={place.id} place={place} selected={selected === place.id} />)}
      {section.limitations?.map((limitation) => <Text key={limitation} style={styles.body}>{limitation}</Text>)}
      {section.intent === 'transport' && <LinkButton url={directionsUrl(section.directions_url)} label="Open journey in Google Maps" primary />}
      {section.sources?.filter((source) => section.intent !== 'transport' || source.name !== 'Google Maps').map((source) => <View key={`${source.name}-${source.url}`}>
        {source.retrieved_at && <Text style={styles.credit}>Retrieved {source.retrieved_at}</Text>}
        <LinkButton url={source.url} label={`Source: ${source.name}`} />
      </View>)}
    </View>)}
    {result.reports?.map((report) => <CivicReportCard key={report.id} report={report} selected={selected === report.id} />)}
    {!weatherOnly && <InlinePlacesMap key={result.request_id || markers.map((place) => `${place.id}:${place.location?.latitude}:${place.location?.longitude}`).join('|')} places={markers} onSelect={setSelected} />}
    <View style={styles.actions}>
      {result.quick_replies?.filter((tile) => ['open_report', 'my_reports'].includes(tile.id) ? Boolean(onCivic) : tile.kind === 'local' || onReply).map((tile) => <Pressable key={tile.id} accessibilityRole="button" accessibilityLabel={tile.label} disabled={busy} style={styles.tile}
        onPress={() => { if (['open_report', 'my_reports'].includes(tile.id)) onCivic?.(tile.id); else if (tile.kind === 'local') setDetails((current) => !current); else if (tile.kind === 'input') { setEdit(tile.id); setValue(''); } else void send(tile.label, tile.id); }}><Text style={styles.actionText}>{tile.label}</Text></Pressable>)}
      {!result.quick_replies?.some((tile) => tile.id === 'show_details') && result.summary && <Pressable accessibilityRole="button" accessibilityLabel="Show details" style={styles.tile} onPress={() => setDetails((current) => !current)}><Text style={styles.actionText}>{details ? 'Hide details' : 'Details & sources'}</Text></Pressable>}
    </View>
    {edit && <View style={styles.actions}><TextInput accessibilityLabel={edit.replace(/_/g, ' ')} placeholder={edit === 'change_budget' ? 'Budget and currency' : edit === 'change_destination' ? 'New destination' : 'Area or landmark'} value={value} onChangeText={setValue} style={styles.edit} />
      <Pressable accessibilityLabel="Apply change" disabled={busy || !value.trim()} style={styles.tile} onPress={() => { const prefix = edit === 'change_budget' ? 'Change budget to ' : edit === 'change_destination' ? 'Change destination to ' : 'Change area to '; void send(prefix + value.trim()); }}><Text style={styles.actionText}>Apply</Text></Pressable>
      <Pressable accessibilityLabel="Cancel change" style={styles.tile} onPress={() => setEdit(null)}><Text style={styles.actionText}>Cancel</Text></Pressable></View>}
    {weatherOnly && <Text style={styles.credit}>Weather data by Open-Meteo · Forecast, not an official warning.</Text>}
    {details && <View>
      {sections.flatMap((section) => section.weather?.hourly || []).map((row) => <Text key={row.time} style={styles.body}>{row.time.slice(11, 16)}–{row.precipitation_interval_end.slice(11, 16)} IST: {row.temperature_2m}°C, rain chance {row.precipitation_probability}%</Text>)}
      <Text style={styles.body}>{result.description}</Text>{result.details.map((detail) => <Text key={detail} style={styles.body}>{detail}</Text>)}</View>}
  </View>;
}

// Match the existing Manus place-list/card/action tokens. Attribution overrides
// are local: normal sans serif, 12sp, #5E5E5E on white, never truncated.
const styles = StyleSheet.create({
  selected: { borderColor: '#E77B4B', borderWidth: 2 }, tile: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 13, paddingVertical: 9, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(20,24,29,0.13)' },
  edit: { minHeight: 44, minWidth: 160, flex: 1, padding: 10, borderWidth: 1, borderRadius: 10, borderColor: 'rgba(20,24,29,0.13)', color: '#14181D', fontFamily: 'Manrope' },
  placeList: { marginTop: 22, gap: 18 }, section: { gap: 11 },
  placeHeading: { color: '#14181D', fontFamily: 'Manrope', fontSize: 17, fontWeight: '700' },
  placeCard: { borderRadius: 16, overflow: 'hidden', backgroundColor: '#FFF', borderWidth: 1, borderColor: 'rgba(20,24,29,0.13)' },
  placeImage: { width: '100%', height: 155 }, imageFallback: { height: 155, alignItems: 'center', justifyContent: 'center', backgroundColor: '#EFEDE7' },
  placeInfo: { padding: 14 }, placeTitle: { color: '#14181D', fontFamily: 'Manrope', fontSize: 14, fontWeight: '700', marginBottom: 4 },
  body: { color: '#73736F', fontFamily: 'Manrope', fontSize: 12, lineHeight: 18 },
  category: { color: '#73736F', fontFamily: 'Manrope', fontSize: 12, marginTop: 6 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 14 },
  actionPrimary: { paddingHorizontal: 13, paddingVertical: 9, borderRadius: 10, backgroundColor: '#14181D', alignSelf: 'flex-start' },
  actionPrimaryText: { color: '#FFF', fontFamily: 'Manrope', fontSize: 11, fontWeight: '700', flexShrink: 1 },
  actionSecondary: { paddingHorizontal: 13, paddingVertical: 9, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(20,24,29,0.13)', alignSelf: 'flex-start' },
  actionText: { color: '#14181D', fontFamily: 'Manrope', fontSize: 12, flexShrink: 1 },
  googleAttribution: { color: '#5E5E5E', fontSize: 12, fontWeight: '400', fontStyle: 'normal', letterSpacing: 0, marginTop: 12, marginBottom: 6, alignSelf: 'flex-start' },
  credit: { color: '#5E5E5E', fontSize: 12, lineHeight: 18 }, credits: { marginTop: 8, gap: 8 },
  author: { flexDirection: 'row', gap: 8, alignItems: 'center' }, authorText: { flex: 1, gap: 4 }, avatar: { width: 28, height: 28, borderRadius: 14 },
});

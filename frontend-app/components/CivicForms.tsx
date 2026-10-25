import { useRef, useState, type ReactNode } from 'react';
import { Alert, Image, Linking, Pressable, Text, TextInput, View, type TextStyle, type ViewStyle, type ImageStyle } from 'react-native';
import { civicRequest, clearSubmission, submissionKey, type CivicReport, type CommunityPost, type ReportDraft, type ResolvedLocation, type SelectedPhoto } from '../api/civic';
import { CivicFeed, CivicReportCard } from './CivicContent';

// Use the actual existing screen tokens passed by App, without a second theme.
type Tokens = Record<string, ViewStyle & TextStyle & ImageStyle>;
type Button = (props: { title: string; onPress: () => void; secondary?: boolean }) => ReactNode;
type Base = { photo: SelectedPhoto | null; onPhoto: () => void; onClose: () => void; styles: Tokens; Button: Button };

export function ReportForm({ draft, photo, onPhoto, onClose, styles, Button }: Base & { draft: ReportDraft }) {
  const [category, setCategory] = useState(draft.category || 'pothole');
  const [description, setDescription] = useState(draft.description || '');
  const [location, setLocation] = useState(draft.location_text || '');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [publicReport, setPublic] = useState(false);
  const [photoPublic, setPhotoPublic] = useState(false);
  const [resolution, setResolution] = useState<ResolvedLocation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState<CivicReport | null>(null);
  const lock = useRef(false);
  const resolve = async () => {
    if (lock.current || !location.trim()) return; lock.current = true; setBusy(true); setError('');
    try {
      const value = await civicRequest<ResolvedLocation>('/v1/civic/resolve-location', { text: location.trim() });
      setResolution(value); setConfirmed(false);
      if (value.place?.location) { setLatitude(String(value.place.location.latitude)); setLongitude(String(value.place.location.longitude)); }
      else { setLatitude(''); setLongitude(''); setError('Location is not precise enough. Enter a specific landmark/address or confirmed coordinates.'); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Location lookup failed.'); }
    finally { lock.current = false; setBusy(false); }
  };
  const submit = async () => {
    if (lock.current) return;
    if (!description.trim() || !location.trim() || !confirmed) { setError('Describe the issue, enter its location and confirm the details before submitting.'); return; }
    if (Boolean(latitude.trim()) !== Boolean(longitude.trim()) || (latitude && (!Number.isFinite(Number(latitude)) || !Number.isFinite(Number(longitude))))) { setError('Supply both valid coordinates, or leave both empty.'); return; }
    lock.current = true; setBusy(true); setError('');
    try {
      const payload = { category: category.trim().toLowerCase().replace(/\s+/g, '_'), description: description.trim(), location_text: location.trim(), latitude: latitude.trim() ? Number(latitude) : null, longitude: longitude.trim() ? Number(longitude) : null, location_confirmed: confirmed && Boolean(latitude.trim() && longitude.trim()), public: publicReport, photo_public: publicReport && photoPublic };
      const idempotency_key = await submissionKey('report', { ...payload, photo: photo?.uri });
      const record = await civicRequest<CivicReport>('/v1/civic/reports', { ...payload, idempotency_key }, photo);
      setSaved(record); void clearSubmission('report').catch(() => {});
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Report was not saved. Please retry.'); }
    finally { lock.current = false; setBusy(false); }
  };
  if (saved) return <View><View style={styles.successCard}><Text style={styles.successMark}>✓</Text><Text style={styles.successTitle}>Report saved in Smart Nagrik.</Text><Text style={styles.successCopy}>{saved.status_label} · {saved.id}</Text></View><CivicReportCard report={saved} /><Button title="Back to city" secondary onPress={onClose} /></View>;
  return <View style={styles.formStack}>
    <Text style={styles.fieldLabel}>ISSUE TYPE</Text><TextInput accessibilityLabel="Issue type" editable={!busy} value={category} onChangeText={setCategory} placeholder="pothole, garbage, streetlight, road damage, waterlogging, other" style={styles.fieldInput} />
    <Text style={styles.fieldLabel}>WHAT HAPPENED?</Text><TextInput accessibilityLabel="Issue description" editable={!busy} value={description} onChangeText={setDescription} multiline maxLength={2000} placeholder="Add one clear detail that will help someone understand the issue." placeholderTextColor="#8C8C86" style={[styles.fieldInput, styles.textArea]} />
    <Text style={styles.fieldLabel}>LOCATION</Text><TextInput accessibilityLabel="Issue location" editable={!busy} value={location} onChangeText={(value) => { setLocation(value); setConfirmed(false); setResolution(null); setLatitude(''); setLongitude(''); }} placeholder="Street, landmark or area" placeholderTextColor="#8C8C86" style={styles.fieldInput} />
    <Button title={busy ? 'Please wait…' : 'Find location'} secondary onPress={() => void resolve()} />
    {resolution?.place && <View><Text style={styles.formNote}>{resolution.place.name} · {resolution.place.address}</Text><Text style={{ color: '#5E5E5E', fontSize: 12 }}>Google Maps</Text>
      {resolution.place.attributions?.map((credit) => <View key={credit.provider}><Text style={styles.formNote}>{credit.provider}</Text>{credit.provider_uri && /^https:\/\//.test(credit.provider_uri) && <Button title={`Source: ${credit.provider}`} secondary onPress={() => void Linking.openURL(credit.provider_uri!).catch(() => Alert.alert('Source unavailable', 'Please try again.'))} />}</View>)}
    </View>}
    {resolution?.status === 'ambiguous' && resolution.candidates?.map((candidate) => <Text key={candidate.id} style={styles.formNote}>{candidate.name} · {candidate.address}</Text>)}
    <Text style={styles.formNote}>A locality centre is not the exact issue location. Verify the point below; adjust coordinates if needed. Without coordinates, your report is saved without a map marker.</Text>
    <TextInput accessibilityLabel="Issue latitude" editable={!busy} value={latitude} onChangeText={(value) => { setLatitude(value); setConfirmed(false); }} keyboardType="decimal-pad" placeholder="Latitude (optional)" style={styles.fieldInput} />
    <TextInput accessibilityLabel="Issue longitude" editable={!busy} value={longitude} onChangeText={(value) => { setLongitude(value); setConfirmed(false); }} keyboardType="decimal-pad" placeholder="Longitude (optional)" style={styles.fieldInput} />
    <Pressable accessibilityRole="checkbox" accessibilityLabel="Confirm issue location" accessibilityState={{ checked: confirmed }} disabled={busy} onPress={() => setConfirmed((value) => !value)} style={styles.uploadButton}><Text>{confirmed ? '✓' : '□'} I confirm these issue details and location.</Text></Pressable>
    <Pressable accessibilityRole="checkbox" accessibilityLabel="Publish report to community" accessibilityState={{ checked: publicReport }} disabled={busy} onPress={() => setPublic((value) => !value)} style={styles.uploadButton}><Text>{publicReport ? '✓' : '□'} Make description and location public in the community.</Text></Pressable>
    <Pressable disabled={busy} accessibilityLabel="Choose report photo" onPress={onPhoto} style={styles.uploadButton}><Text>＋ Add photo {photo ? '· attached' : '(optional)'}</Text></Pressable>
    {photo && <Image source={{ uri: photo.uri }} style={styles.attachedPhoto} />}
    {photo && publicReport && <Pressable accessibilityRole="checkbox" accessibilityLabel="Publish report photo" accessibilityState={{ checked: photoPublic }} disabled={busy} onPress={() => setPhotoPublic((value) => !value)} style={styles.uploadButton}><Text>{photoPublic ? '✓' : '□'} I have permission to share this photo publicly.</Text></Pressable>}
    {error ? <Text accessibilityRole="alert" style={styles.formNote}>{error}</Text> : null}
    <Button title={busy ? 'Saving…' : 'Submit report'} onPress={() => void submit()} />
    <Text style={styles.formNote}>This saves a Smart Nagrik report. It is not an official government submission. Private reports/photos stay visible to this installation only.</Text>
  </View>;
}

export function PostForm({ photo, onPhoto, onClose, styles, Button }: Base) {
  const [text, setText] = useState(''); const [location, setLocation] = useState(''); const [reportId, setReportId] = useState('');
  const [photoPublic, setPhotoPublic] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [saved, setSaved] = useState<CommunityPost | null>(null); const [feed, setFeed] = useState(false); const lock = useRef(false);
  const submit = async () => {
    if (lock.current) return; if (!text.trim()) { setError('Enter a post before publishing.'); return; }
    lock.current = true; setBusy(true); setError('');
    try {
      const payload = { text: text.trim(), location_text: location.trim(), report_id: reportId.trim() || null, public: true, photo_public: photoPublic };
      const idempotency_key = await submissionKey('post', { ...payload, photo: photo?.uri });
      setSaved(await civicRequest<CommunityPost>('/v1/community/posts', { ...payload, idempotency_key }, photo)); void clearSubmission('post').catch(() => {});
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Post was not saved.'); }
    finally { lock.current = false; setBusy(false); }
  };
  if (feed) return <View><CivicFeed posts /><Button title="Back to post" secondary onPress={() => setFeed(false)} /></View>;
  if (saved) return <View><View style={styles.successCard}><Text style={styles.successMark}>✓</Text><Text style={styles.successTitle}>Post saved to the community.</Text><Text style={styles.successCopy}>Reference {saved.id}</Text></View><Button title="View community posts" onPress={() => setFeed(true)} /><Button title="Back to city" secondary onPress={onClose} /></View>;
  return <View style={styles.formStack}>
    <Text style={styles.fieldLabel}>WHAT SHOULD PEOPLE KNOW?</Text><TextInput accessibilityLabel="Post text" value={text} onChangeText={setText} editable={!busy} maxLength={2000} multiline placeholder="Describe your observation" placeholderTextColor="#8C8C86" style={[styles.fieldInput, styles.textArea]} />
    <Text style={styles.fieldLabel}>LOCATION</Text><TextInput accessibilityLabel="Post location" value={location} onChangeText={setLocation} editable={!busy} placeholder="Landmark or area (optional)" style={styles.fieldInput} />
    <TextInput accessibilityLabel="Linked report reference" value={reportId} onChangeText={setReportId} editable={!busy} placeholder="Existing report reference (optional)" style={styles.fieldInput} />
    <Pressable accessibilityLabel="Choose post photo" disabled={busy} onPress={onPhoto} style={styles.uploadButton}><Text>＋ Add image {photo ? '· attached' : '(optional)'}</Text></Pressable>{photo && <Image source={{ uri: photo.uri }} style={styles.attachedPhoto} />}
    {photo && <Pressable accessibilityRole="checkbox" accessibilityLabel="Publish post photo" accessibilityState={{ checked: photoPublic }} disabled={busy} onPress={() => setPhotoPublic((value) => !value)} style={styles.uploadButton}><Text>{photoPublic ? '✓' : '□'} I have permission to share this photo publicly.</Text></Pressable>}
    {error ? <Text accessibilityRole="alert" style={styles.formNote}>{error}</Text> : null}
    <Button title={busy ? 'Publishing…' : 'Publish post'} onPress={() => void submit()} /><Button title="View community posts" secondary onPress={() => setFeed(true)} />
    <Text style={styles.formNote}>Publishing shares this text and location publicly as a community observation, not an official warning. Linking a report reuses its saved record.</Text>
  </View>;
}

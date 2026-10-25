import { Component, type ReactNode, useEffect, useState } from 'react';
import { Alert, BackHandler, Linking, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import Constants from 'expo-constants';
import type { PlaceRecord } from '../api/chat';
import { initialRegion, mapPoints } from './mapRegion';
import { directionsUrl, httpsUrl } from '../api/chat';
import { CivicReportCard } from './CivicContent';

function Fallback() {
  return <View style={styles.fallback}><Text style={styles.copy}>Map unavailable. Place cards and Directions are still available.</Text></View>;
}

class MapBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <Fallback /> : this.props.children; }
}

function NativeMap({ places, onSelect }: { places: PlaceRecord[]; onSelect?: (id: string) => void }) {
  const [region] = useState(() => initialRegion(places));
  const [points] = useState(() => mapPoints(places));
  const [loaded, setLoaded] = useState(false);
  const [expired, setExpired] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState<PlaceRecord | null>(null);
  useEffect(() => {
    if (!expanded) return;
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => { setExpanded(false); return true; });
    return () => subscription.remove();
  }, [expanded]);
  useEffect(() => {
    if (loaded) return;
    const timer = setTimeout(() => setExpired(true), 12000);
    return () => clearTimeout(timer);
  }, [loaded]);
  if (expired || !region) return <Fallback />;
  // Loaded within the error boundary: an older binary missing this native module
  // must not prevent cards from rendering. Never request location permission.
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- guarded native loading must occur inside MapBoundary
  const { default: MapView, Marker, PROVIDER_GOOGLE } = require('react-native-maps') as typeof import('react-native-maps');
  const color = (place: PlaceRecord) => ({ origin: '#2F8066', destination: '#C95D30', accommodation: '#8B5C9E', restaurant: '#C95D30', service: '#397D99', metro_station: '#8B5C9E', bus_stop: '#397D99', civic: '#C95D30' }[place.marker_type || (place.category === 'accommodation' ? 'accommodation' : place.category === 'food_places' ? 'restaurant' : 'service')]);
  const selectedUrl = selected && directionsUrl(selected.directions_url, selected.place_id || selected.id);
  return <View>
    <Pressable accessibilityRole="button" accessibilityLabel={expanded ? 'Collapse map' : 'Explore map'} accessibilityState={{ expanded }} style={styles.toggle} onPress={() => setExpanded((value) => !value)}><Text style={styles.copy}>{expanded ? 'Collapse map −' : 'Explore map +'}</Text></Pressable>
    <View style={[styles.mapContainer, expanded && styles.expanded]} accessibilityLabel="Map of returned places">
    <MapView provider={PROVIDER_GOOGLE} style={[styles.map, expanded && styles.expanded]} initialRegion={region}
      scrollEnabled={expanded} zoomEnabled={expanded} rotateEnabled={false} pitchEnabled={false}
      zoomTapEnabled={false} toolbarEnabled={false} showsUserLocation={false} showsMyLocationButton={false}
      onMapLoaded={() => setLoaded(true)}>
      {points.map((place) => <Marker key={place.id} identifier={place.id} coordinate={place.location!} title={place.name} description={place.address} pinColor={color(place)} tracksViewChanges={false}
        onPress={() => { setSelected(place); onSelect?.(place.place_id || place.id); }}>
        {place.itinerary_order ? <View style={styles.numberPin}><Text style={styles.numberText}>{place.itinerary_order}</Text></View> : null}
      </Marker>)}
    </MapView>
    {!expanded && <Pressable style={styles.previewTouch} accessibilityRole="button" accessibilityLabel="Expand map preview" onPress={() => setExpanded(true)} />}
    {!loaded && <Text style={styles.loading}>Loading map…</Text>}
    </View>
    {expanded && <Text style={styles.copy}>Pan and pinch to explore. Markers show places, not a calculated route. Tap a marker to inspect it.</Text>}
    {expanded && selected?.civic_report && <CivicReportCard report={selected.civic_report} />}
    {expanded && selected && !selected.civic_report && <View style={styles.selection}><Text style={styles.heading}>{selected.name}</Text><Text style={styles.copy}>{selected.address}</Text><Text style={styles.credit}>Google Maps</Text>
      {selected.attributions.map((credit, index) => <View key={`${credit.provider}-${index}`}><Text style={styles.credit}>{credit.provider}</Text>
        {httpsUrl(credit.provider_uri) && <Pressable accessibilityRole="link" accessibilityLabel={`Source: ${credit.provider}`} style={styles.toggle} onPress={() => { void Linking.openURL(credit.provider_uri!).catch(() => Alert.alert('Could not open source', 'Please try again with a browser available.')); }}><Text style={styles.copy}>View source</Text></Pressable>}
      </View>)}
      {selectedUrl && <Pressable accessibilityRole="link" accessibilityLabel={`Directions to ${selected.name}`} style={styles.toggle} onPress={() => { void Linking.openURL(selectedUrl).catch(() => Alert.alert('Could not open directions', 'Please try again with Google Maps or a browser available.')); }}><Text style={styles.copy}>Directions</Text></Pressable>}
      <Text style={styles.copy}>The matching result card is highlighted when available.</Text>
    </View>}
  </View>;
}

export default function InlinePlacesMap({ places, onSelect }: { places: PlaceRecord[]; onSelect?: (id: string) => void }) {
  if (!mapPoints(places).length) return null;
  const configured = Platform.OS === 'android' && (Constants.executionEnvironment === 'storeClient' || Constants.expoConfig?.extra?.androidMapsConfigured === true);
  return <View style={styles.section}>
    <Text style={styles.heading}>Places on the map</Text>
    {configured ? <MapBoundary><NativeMap places={places} onSelect={onSelect} /></MapBoundary> : <Fallback />}
  </View>;
}

const styles = StyleSheet.create({
  numberPin: { minWidth: 30, height: 30, borderRadius: 15, backgroundColor: '#14181D', alignItems: 'center', justifyContent: 'center' }, numberText: { color: '#FFF', fontSize: 14, fontWeight: '700' },
  expanded: { height: 400 }, toggle: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 10, backgroundColor: '#EFEDE7', marginBottom: 6 },
  previewTouch: { position: 'absolute', top: 0, bottom: 32, left: 0, right: 0 }, selection: { padding: 14, backgroundColor: '#FFF', borderRadius: 16, gap: 6, marginTop: 8 },
  credit: { fontSize: 12, color: '#5E5E5E', fontWeight: '400', letterSpacing: 0 },
  section: { gap: 10 }, heading: { color: '#14181D', fontFamily: 'Manrope', fontSize: 17, fontWeight: '700' },
  mapContainer: { height: 180, borderRadius: 19, overflow: 'hidden', backgroundColor: '#EFEDE7' }, map: { width: '100%', height: 180 },
  loading: { position: 'absolute', top: 8, alignSelf: 'center', padding: 4, backgroundColor: '#FFF', color: '#73736F', fontSize: 12 },
  fallback: { minHeight: 64, padding: 14, borderRadius: 16, backgroundColor: '#EFEDE7' }, copy: { color: '#73736F', fontFamily: 'Manrope', fontSize: 12, lineHeight: 18 },
});

import type { PlaceRecord } from '../api/chat';

export function mapPoints(places: PlaceRecord[]) {
  return places.filter((place) => place.location && Number.isFinite(place.location.latitude) && Number.isFinite(place.location.longitude) &&
    place.location.latitude >= 12.8 && place.location.latitude <= 13.2 && place.location.longitude >= 77.4 && place.location.longitude <= 77.8)
    .filter((place, index, all) => all.findIndex((other) => other.id === place.id) === index).slice(0, 6);
}

export function initialRegion(places: PlaceRecord[]) {
  const points = mapPoints(places);
  if (!points.length) return null;
  const latitudes = points.map((place) => place.location!.latitude);
  const longitudes = points.map((place) => place.location!.longitude);
  const lowLat = Math.min(...latitudes), highLat = Math.max(...latitudes);
  const lowLon = Math.min(...longitudes), highLon = Math.max(...longitudes);
  return { latitude: (lowLat + highLat) / 2, longitude: (lowLon + highLon) / 2,
    latitudeDelta: Math.max(0.015, (highLat - lowLat) * 1.5), longitudeDelta: Math.max(0.015, (highLon - lowLon) * 1.5) };
}

import type { PlaceRecord } from '../api/chat';

// Web/unsupported platforms keep cards and external Directions, without using
// Apple/other map tiles to display Google Places content.
export default function InlinePlacesMap({ places }: { places: PlaceRecord[]; onSelect?: (id: string) => void }) {
  void places;
  return null;
}

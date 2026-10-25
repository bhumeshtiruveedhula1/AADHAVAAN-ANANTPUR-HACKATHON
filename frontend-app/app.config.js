// Separate Android-restricted Maps SDK key, injected only when building a binary.
// Never use GOOGLE_PLACES_API_KEY (the server credential) here.
module.exports = ({ config }) => {
  const mapsKey = process.env.GOOGLE_MAPS_ANDROID_API_KEY?.trim();
  const configured = Boolean(mapsKey && !mapsKey.startsWith('YOUR_'));
  return {
    ...config,
    plugins: [...(config.plugins || []), ...(configured ? [['react-native-maps', { androidGoogleMapsApiKey: mapsKey }]] : [])],
    extra: { ...config.extra, androidMapsConfigured: configured },
  };
};

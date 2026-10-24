# Smart Nagrik Mobile

This folder is the Android-ready Expo / React Native conversion of Smart Nagrik. It is a real mobile source project, not a website wrapper. The initial version uses local mock data for the chat results, city selector, profile, More menu, complaint, post, community feed, alerts, emergency contacts, and WhatsApp handoff demo. Backend APIs, authentication, official complaint submission, and live city data remain integration work for the next phase.

## Open in VS Code

1. Install Node.js LTS and VS Code.
2. Install Expo Go on your Android phone from Google Play.
3. Open this `mobile-app` folder in VS Code.
4. Run `npm install` if `node_modules` is not already present.
5. Start the app with `npx expo start`.
6. Scan the QR code with Expo Go. Keep the phone and computer on the same Wi-Fi network. If the network blocks LAN discovery, run `npx expo start --tunnel`.

For an Android development build, install Android Studio and an Android SDK, then run `npx expo run:android`. An APK/AAB release should be created through an Expo/EAS build workflow or Android Studio after the backend and production configuration are connected.

## Included assets

- `assets/images/hero-bengaluru.jpg`: Bengaluru Vidhana Soudha hero photograph.
- `assets/images/icon.png`: Smart Nagrik launcher icon.
- `assets/images/splash-icon.png`: splash mark.
- `assets/images/android-icon-*.png`: Android adaptive icon layers.
- `assets/fonts/Manrope-Variable.ttf`: interface and display font.
- `assets/fonts/DMMono-Regular.ttf`: utility and metadata font.
- `assets/fonts/DMMono-Medium.ttf`: stronger utility labels.

## Main source

- `App.tsx`: complete mobile prototype and local interaction state.
- `app.json`: Expo identity, Android package name, icon, splash, and permissions/config.
- `package.json`: Expo SDK dependencies.

## Important limitation

The current app demonstrates frontend behavior with local mock data. It does not yet send real chatbot requests, create official government complaints, persist accounts, load live maps, or connect to WhatsApp. Those services can be added without redesigning the main screen.

# G.I.L. for Android — standalone

The full GIL experience on the phone, with **no PC required**. GIL's web UI is
bundled into the APK and served by a small HTTP server running inside the app, so
the chat you know runs natively; the brain calls Groq and the actions use Android.

## What GIL can do on the phone

Open apps, links and settings · search the web · show places on the map · draft a
text or an email · start a call · set alarms and timers · open the camera · copy
and share text · talk out loud (TTS) · answer anything.

**Android limits, honestly:** a phone app cannot get the total system control GIL
has on Windows. There is no arbitrary shell, and apps cannot read each other's
private data. Everything above works through Android's sanctioned Intent APIs.

## Build

Needs Android Studio (or Android SDK + JDK 17).

1. Open this `android/` folder in Android Studio.
2. Let Gradle sync.
3. **Build -> Build Bundle(s) / APK(s) -> Build APK(s)**
4. APK: `app/build/outputs/apk/debug/app-debug.apk`

CLI, if the SDK is configured: `cd android && ./gradlew assembleDebug`

## Install & first run

Copy the APK to the phone and tap it (allow install from unknown sources), or
`adb install app-debug.apk`.

On first launch GIL asks for a **free Groq API key** (console.groq.com/keys).
Send it in the chat as:

```
/key gsk_your_key_here
```

That's the only setup. The key is stored on the phone.

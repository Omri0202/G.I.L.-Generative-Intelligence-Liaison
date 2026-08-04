# G.I.L. for Android — standalone

The full GIL experience on the phone, with **no PC required**. GIL's web UI is
bundled into the APK and served by a small HTTP server running inside the app, so
the chat you know runs natively; the brain calls Groq and the actions use Android.

## What GIL can do on the phone

**Out of the box:** open apps, links and settings · search the web · show places
on the map · draft a text or an email · start a call · set alarms and timers ·
open the camera · copy and share text · talk out loud · answer anything.

**With "All files access"** (GIL asks; you grant it in Settings): read and write
your Documents, Downloads and Photos — "what's in my CV", "save this as
notes.md", "find that PDF".

**With "Screen control"** (an Accessibility Service; you switch it on in
Settings > Accessibility): GIL can read what is on screen in *other* apps and
tap, type and scroll for you — "open WhatsApp and reply to Omri", "summarise
what's on screen". Revoke it any time in the same place.

### The honest limits

Android sandboxes apps far more tightly than Windows, and these cannot be worked
around by any app:

- **No arbitrary shell** — GIL can't install/uninstall apps, change system
  settings directly or kill processes. It opens the relevant screen; you decide.
- **No reading another app's private data** — WhatsApp's message database,
  Instagram's cache and saved logins are walled off from every other app.
- **No silent screen recording** — that always needs an explicit consent dialog
  and shows a visible indicator.

Screen control is the closest thing to real automation Android allows, and it is
deliberately gated behind a switch only you can flip.

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

# G.I.L. for Android

A thin native shell around GIL's web chat. GIL's brain runs on your PC — this is
the phone client, so the PC must be awake, running GIL, and on the same Wi-Fi.

## Build

Requires Android Studio (or the Android SDK + JDK 17).

1. Open this `android/` folder in **Android Studio** (File → Open).
2. Let it sync Gradle (it downloads what it needs the first time).
3. **Build → Build Bundle(s) / APK(s) → Build APK(s)**.
4. The APK lands in `app/build/outputs/apk/debug/app-debug.apk`.

Command line, if the SDK is already set up:

```
cd android
./gradlew assembleDebug
```

## Install

Copy the APK to your phone and open it (allow "install from unknown sources"),
or with the phone plugged in and USB debugging on:

```
adb install app/build/outputs/apk/debug/app-debug.apk
```

## First run

1. On the PC, in GIL's chat, run **/mobile**.
2. Copy the link it shows (the same one the QR encodes).
3. Paste it into the app and tap **Connect**.

The link contains your private pairing key, so the app is authorised from then
on. Long-press the back button to point the app at a different server.

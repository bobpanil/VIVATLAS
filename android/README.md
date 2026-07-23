# VIVATLAS Android shell

A deliberately thin **Kotlin WebView** app. It is two things at once:

1. **The mobile/tablet UI** — a full-screen WebView onto your VIVATLAS server, so
   you get the same responsive web UI as a real installed app.
2. **A share target** — scroll Reddit / Facebook / Chrome → **Share → VIVATLAS**,
   and the link is captured straight into your library (background `POST
   /api/ext/add`), with a "Added to VIVATLAS" toast. No separate native login: the
   share reuses the WebView's own session cookie as a Bearer token.

There is **no APK checked in** — you build it from this source. Nothing here needs
changing to build; the server address is entered on first launch and stored on the
device.

## What's inside

```
android/
  settings.gradle.kts, build.gradle.kts, gradle.properties
  app/
    build.gradle.kts
    src/main/AndroidManifest.xml            # MainActivity + ShareActivity (ACTION_SEND)
    src/main/java/com/vivatlas/app/
      MainActivity.kt                        # the WebView host
      ShareActivity.kt                       # share-sheet capture → /api/ext/add
      Prefs.kt                               # remembers the server URL
    src/main/res/                            # layout, theme, strings, icon, net-security
```

- **minSdk 24** (Android 7) · **targetSdk/compileSdk 34** · Kotlin 1.9 · AGP 8.5.2.
- Only dependencies: `androidx.core`, `androidx.appcompat`, `androidx.webkit`.

## 1. Toolchain (this machine had none)

Install **Android Studio** (bundles the JDK, Android SDK, `adb`, the emulator, and
the AVD manager): <https://developer.android.com/studio>. Then, inside Studio:

- SDK Manager → install **Android 14 (API 34)** platform + **platform-tools**.
- Device Manager → create one **AVD** (e.g. Pixel 7, API 34).

Headless / CLI alternative (no IDE): install a **JDK 17** and the Android
**command-line tools**, then:

```bash
sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
```

## 2. Build

**Easiest:** open the `android/` folder in Android Studio — it provisions the Gradle
wrapper automatically — then **Run** onto your AVD or phone.

**CLI:** first generate the wrapper once (Studio does this for you, or run it with a
system Gradle ≥ 8.7):

```bash
cd android
gradle wrapper --gradle-version 8.7
./gradlew assembleDebug
```

The APK lands at `android/app/build/outputs/apk/debug/app-debug.apk`.

Install it on a connected device/emulator:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## 3. First run

The app asks for your **server URL**:

- **Emulator →** `http://10.0.2.2:8710` (10.0.2.2 is the host machine from inside the
  emulator).
- **Phone on the same Wi-Fi →** `http://<your-PC-LAN-IP>:8710`.
- **Public server →** `https://vivatlas.example.com`.

Log in once in the WebView; that session cookie is what the share target reuses.

To change it later: hardware **Back** at the home page → **Change server**.

## 4. Live-preview loop (iterate without reinstalling)

Run the server bound to all interfaces so both the emulator and your phone can reach
it:

```bash
.venv/Scripts/python -m uvicorn vivatlas.api:app --host 0.0.0.0 --port 8710
```

- **Web-UI changes** (templates, CSS, JS): just **reload** the WebView (pull-to-
  refresh or reopen) — no rebuild, no reinstall.
- **Native shell changes** (Kotlin/manifest): `./gradlew assembleDebug && adb install
  -r …`, or just hit **Run** in Studio.
- **Inspect** the WebView from desktop Chrome at `chrome://inspect` (debugging is
  enabled in debug builds).

## 5. Test the share flow

From Chrome/Reddit/Facebook on the device → **Share** → **VIVATLAS**:

- Logged in → toast **"Added to VIVATLAS"**; the card appears in your library.
- Not logged in → the app opens the **Add** form pre-filled with the link
  (`/add?source=…`); the server's login-then-continue flow takes over, so the share
  isn't lost.

## Security notes (see also Phase 4 review)

- JavaScript is enabled only for your own trusted origin; **file/content access is
  off**. Non-server links open in the system browser, never in-app.
- The session token is read from the WebView cookie and sent only to your server's
  `/api/ext/add`; it is never logged.
- Cleartext HTTP is allowed by default because self-hosting on a LAN commonly uses
  plain HTTP (`res/xml/network_security_config.xml`). If you serve over HTTPS, set
  `cleartextTrafficPermitted="false"` there.
- The app icon is the brand mark; regenerate proper per-density icons anytime with
  Studio's **Image Asset** tool if you want crisper legacy (API < 26) icons.

# VIVATLAS Android shell

> **Toolchain is already installed on this PC** (headless, no Android Studio):
> JDK 17 `D:\Android\jdk\jdk-17.0.19+10` · SDK `D:\Android\sdk` · Gradle
> `D:\Android\gradle-8.7` · AVD `vivatlas` (Pixel 6, API 34). A debug APK is built
> at `app/build/outputs/apk/debug/app-debug.apk`. Rebuild + install:
>
> ```bash
> JAVA_HOME=/d/Android/jdk/jdk-17.0.19+10 /d/Android/gradle-8.7/bin/gradle -p android assembleDebug
> /d/Android/sdk/platform-tools/adb.exe -s <device> install -r android/app/build/outputs/apk/debug/app-debug.apk
> ```
>
> Phone (wireless adb): pair once with the code from the phone's *Wireless
> debugging → Pair device with pairing code*, then `adb connect <lan-ip>:<port>`.
> For the phone, set the server URL to the host's **LAN IP** (`http://<host-lan-ip>:8710`),
> and run the server with `--host 0.0.0.0`.

**Licence:** the app in this directory is [Apache-2.0](LICENSE) — free software, so it
can be built and distributed by F-Droid and friends. The server it talks to stays under
the repository's [Business Source License](../LICENSE); the two are separate works.

A deliberately thin **Kotlin WebView** app. It is two things at once:

1. **The mobile/tablet UI** — a full-screen WebView onto your VIVATLAS server, so
   you get the same responsive web UI as a real installed app.
2. **A share target** — scroll Reddit / Facebook / Chrome → **Share → VIVATLAS**,
   and an "Add to VIVATLAS" sheet shows the link and which zone it will land in —
   **Private** or **Public** — starting from your standing choice in App settings.
   Tap **Add** and it goes (background `POST /api/ext/add`), with a toast that names
   the zone. No separate native login: the share reuses the WebView's own session
   cookie as a Bearer token.

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
      ShareActivity.kt                       # share-sheet capture (zone sheet) → /api/ext/add
      QrCode.kt                              # reads the scanned sign-in code
      Prefs.kt                               # remembers the server URL
    src/main/res/                            # layout, theme, strings, icon, net-security
```

- **minSdk 24** (Android 7) · **targetSdk/compileSdk 34** · Kotlin 1.9 · AGP 8.5.2.
- Only dependencies: `androidx.core`, `androidx.appcompat`, `androidx.webkit`, and
  `zxing-android-embedded` for reading the sign-in QR (it carries its own decoder,
  so there is no Google Play services dependency).

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

Sign in once; that session cookie is what the share target reuses.

**Signing in without typing the password.** On a computer already signed in, open
**Settings → Sign in on your phone** and press *Show the code*. On the phone, tap
**Scan a sign-in code** on the sign-in screen and point it at the screen. The code
carries the server address as well as the pass, so a fresh install needs nothing
typed at all — not even the address. It is good once and for 90 seconds; anyone who
reads it signs in as you, so let it expire before you walk away. Behind a proxy or
tunnel the server needs its site address set (**Admin → Integrations**) before it will
show a code — the code must carry the public https address, and the app talks https to
any public host regardless of what the code says.

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

## 5. Releasing

A release is a git tag: `android-v<versionName>`, matching `versionName` in
`app/build.gradle.kts` (the workflow refuses a tag that doesn't). Pushing the tag runs
`.github/workflows/android.yml`, which builds the signed release APK and attaches it to
a GitHub Release — that is what Obtainium and IzzyOnDroid watch for updates.

```bash
# bump versionCode and versionName in app/build.gradle.kts, add a changelog at
# fastlane/metadata/android/en-US/changelogs/<versionCode>.txt, commit, then:
git tag -a android-v1.5 -m "Android app 1.5" && git push origin android-v1.5
```

Signing uses one keystore for the life of the app — an APK signed with a different key
cannot install over the previous one. It lives outside the repository, in the GitHub
secrets `ANDROID_KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`. A
local `./gradlew assembleRelease` with none of those set produces an unsigned APK, which
is fine for checking the build and useless for installing.

## 6. Test the share flow

From Chrome/Reddit/Facebook on the device → **Share** → **VIVATLAS**:

- Logged in → the **Add to VIVATLAS** sheet, with the zone preset from App settings.
  Change it for this one share if you like, tap **Add**, and the toast says where it
  went — *"Added to VIVATLAS — private"* or *"Added to the shared catalogue"*. The
  card appears in your library. **Cancel** (or Back) drops the share without saving.
- Not logged in → the app opens the **Add** form pre-filled with the link
  (`/add?source=…`); the server's login-then-continue flow takes over, so the share
  isn't lost.

## Security notes (see also Phase 4 review)

- JavaScript is enabled only for your own trusted origin; **file/content access is
  off**. Non-server links open in the system browser, never in-app.
- The session token is read from the WebView cookie and sent only to your server's
  `/api/ext/add`; it is never logged.
- The camera is requested only when **Scan a sign-in code** is tapped, and used for
  nothing else. A scanned code is checked before anything is sent: http/https, a
  `/qr/<token>` path and a token-shaped token, or it is refused unsent (`QrCode.kt`).
- Cleartext HTTP is allowed by default because self-hosting on a LAN commonly uses
  plain HTTP (`res/xml/network_security_config.xml`). If you serve over HTTPS, set
  `cleartextTrafficPermitted="false"` there.
- The app icon is the brand mark; regenerate proper per-density icons anytime with
  Studio's **Image Asset** tool if you want crisper legacy (API < 26) icons.

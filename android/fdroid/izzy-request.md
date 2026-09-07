# IzzyOnDroid — app inclusion request, filled in

Where to file: https://codeberg.org/IzzyOnDroid/repodata/issues/new?template=.forgejo%2Fissue_template%2Fapp-inclusion-request.yaml
(You need a Codeberg account. The form is a set of boxes; paste each block below into the box with the same label.)

Read this first — their App Inclusion Policy has an "AI policy" section:
"Vibe-coded apps will be rejected … the code itself should be free of it … Any lack
of transparency discovered by us can lead to the project being degraded to rejected
state." The Android app's code was written with Claude Code, and the form asks about
that directly. The answers below say so honestly. Expect a rejection on that ground;
a false answer would be worse than no listing.

---

## Title

    [AppRequest] VIVATLAS

## Guidelines (checkboxes)

- [x] I am the developer of the app.
- [ ] The app complies with the App Inclusion Policy.   ← see the AI policy note above; tick only if you disagree with my reading
- [x] The app is not already listed in the repo or issue tracker.
- [x] The Fastlane folder is available in the app's repo.

## Link to the source code

    https://github.com/bobpanil/VIVATLAS

## Link to app in another app store

    https://gitlab.com/fdroid/fdroiddata/-/merge_requests/48112

(F-Droid inclusion is pending; no other store.)

## License used

    Apache-2.0

(The Android app, everything under `android/`, is Apache-2.0. The server it talks to is a separate program under BSL 1.1 — see Further Notices.)

## Categories

    Development, Internet

## Summary

Client for a self-hosted VIVATLAS server: a catalogue of the AI skills, agents, MCP servers and tools scattered across your Git repositories, in your pocket.

## Description

VIVATLAS is a self-hosted catalogue for the skills, agents, MCP servers and tools scattered across your Git repositories and saved links. Each one becomes a card with a picture, a description written for a person, and tags — searchable by meaning, not just by name.

This app is the phone end of it. It needs a VIVATLAS server you run yourself (https://github.com/bobpanil/VIVATLAS).

- The whole catalogue, as a proper app rather than a browser tab.
- Share → VIVATLAS from any app to save a link. A small sheet shows what you're saving and lets you choose private or public before it goes.
- Sign in by scanning a QR code from a browser that's already signed in — a long password never has to meet a phone keyboard.
- No accounts here, no analytics, no Google services. It talks to your server and nothing else.

Permissions: INTERNET and ACCESS_NETWORK_STATE (to reach your server), CAMERA (only while scanning the sign-in QR; the camera feature is marked as not required).

## Build instructions

    git clone https://github.com/bobpanil/VIVATLAS.git
    cd VIVATLAS/android
    ./gradlew assembleRelease
    # → app/build/outputs/apk/release/app-release-unsigned.apk

Requirements: JDK 17, Android SDK with platform 34 and build-tools 34. Gradle wrapper 8.7, AGP 8.5.2, Kotlin. No NDK, no native code, no submodules, no srclibs.

Release signing is taken from the environment (`ANDROID_KEYSTORE_FILE`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`); with none set, the release APK is left unsigned. The published APK is built by `.github/workflows/android.yml` on tags matching `android-v*` and attached to the GitHub release, signed with the developer's release key:

    SHA-256 of the signing certificate:
    71:49:8F:B3:4B:12:3E:7A:DE:FB:27:13:2A:4A:E1:E9:C0:28:A2:E2:74:D0:19:29:77:FF:F5:E9:8C:F0:5C:65

Current release: https://github.com/bobpanil/VIVATLAS/releases/tag/android-v1.4 — `vivatlas-android-1.4.apk` (3.1 MB, versionCode 5), with a `.sha256` next to it.

## AI Tools Usage

### Assistance Level

    Dominant – Most code or content was "AI"-generated

### "AI" Tool(s)

    Claude (Claude Code)

### What did the tools help with, and how?

The Android app is a small Kotlin WebView shell (share target, QR sign-in, a settings screen for the server address). Its code was written by Claude Code from my instructions, feature by feature, with me directing the design, reviewing the result on the device and asking for changes. The same goes for the release workflow and the Fastlane texts. I am stating this plainly because your policy asks for it; I understand it will probably mean a rejection, and I'd rather be told that than be listed under a false answer.

### AI Accountability

- [ ] The human developer(s) reviewed and edited all "AI"-generated outputs   ← tick only if that's true for you
- [x] The human developer(s) ran manual tests and manually verified all changes

## Further Notices

- The server side (https://github.com/bobpanil/VIVATLAS, everything under `src/`) is a separate, self-hosted program under the Business Source License 1.1 — free to run and modify, converting to Apache-2.0 in 2030. The app is useless without a server, which is why the F-Droid metadata carries the NonFreeNet anti-feature. The app itself is Apache-2.0 and contains no BSL code.
- The server can, at the admin's choice, use an AI model (Google Gemini or a local Ollama) to write card descriptions and pick card pictures. The app does not talk to any AI service; it only talks to the user's own server.
- `usesCleartextTraffic` is permitted via `network_security_config.xml`. Reason: the server is normally self-hosted on a home LAN, frequently over plain HTTP to a NAS. The config file says how to tighten it for HTTPS-only setups.
- The app is more than a bookmark: it is a system share target with a private/public choice, and it signs in by scanning a QR code with the camera — neither of which a browser bookmark or PWA can do.
- Third-party dependencies: AndroidX (core, appcompat, webkit, swiperefreshlayout, splashscreen) and ZXing Android Embedded 4.3.0 (Apache-2.0) for the QR scanner. No trackers, no analytics, no ads, no Google Play services.
- Not debuggable, not testOnly; the APK is signed by the developer's release key and attached to a GitHub tagged release, so reproducible-build checks are possible.

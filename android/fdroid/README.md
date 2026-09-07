# Submitting the app to F-Droid

`com.vivatlas.app.yml` is the metadata file for [fdroiddata](https://gitlab.com/fdroid/fdroiddata),
the repository F-Droid builds from. It is kept here in the exact form their tooling
wants (`fdroid rewritemeta` canonical form, `fdroid lint` clean), so the submission is
a copy, not a rewrite.

To submit:

1. Fork fdroiddata on GitLab and clone the fork.
2. Copy this file to `metadata/com.vivatlas.app.yml`.
3. Commit as `New app: VIVATLAS` and open a merge request against `master`.
4. Their CI runs lint and a build; a reviewer then reads it. Weeks, not days, is normal.

What the file says, and why:

- **License: Apache-2.0** — the app's licence (`../LICENSE`). The server is BSL, which is
  why the listing carries the **NonFreeNet** anti-feature: the app is a client for a
  service that isn't free software, even though it's one you run yourself.
- **Builds** — one entry per release, built from the tag `android-v<versionName>` in
  `android/app` with plain Gradle. F-Droid builds and signs with its own key, so an
  F-Droid install and a GitHub-release install can't update over each other; that is
  normal and expected.
- **UpdateCheckMode: Tags ^android-v** with **AutoUpdateMode: Version** — F-Droid's
  bot notices a new `android-v*` tag and opens the update merge request itself. Keep
  `versionCode`/`versionName` in `app/build.gradle.kts` in step with the tag.

For a faster first appearance, IzzyOnDroid takes the signed APK straight from the
GitHub Release and reads the descriptions from `../fastlane/`; it needs no file at all,
only a request at https://apt.izzysoft.de/fdroid/ (or an issue on their tracker).

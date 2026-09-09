### Device / Tester Review

Tested `com.meteocompare.app` 1.11.3 (versionCode 28) on a Galaxy S21+ (SM-G996B), Android 15, SDK 35.
APK: `tmp/binaries/com.meteocompare.app_28.binary.apk` from this MR's own CI (job 16117122134),
signer `f7b0432751cd0d183c62a40beb989574b488b290f63889fc177c5f93dfee2ab3`, which matches
`AllowedAPKSigningKeys` in the recipe.

<table>
<thead>
<tr>
<th>Category</th>
<th>Checklist</th>
</tr>
</thead>
<tbody>
<tr>
<td>Basic Function</td>
<td>

- [x] The app can start and work normally.
- [x] The functions in the description are implemented.
- [x] The app has a unique icon (instead of a default one).

</td>
</tr>
<tr>
<td>Policy Compliance</td>
<td>

- [x] Features don't violate F-Droid's Inclusion Policy.
- [x] The Categories field is set properly.
- [x] Doesn't require accepting any terms other than the FOSS license.

</td>
</tr>
<tr>
<td>Permissions</td>
<td>

- [x] The app can be used without granting optional runtime permissions.
- [x] The app doesn't require unnecessary MANAGE_EXTERNAL_STORAGE permission.

</td>
</tr>
<tr>
<td>Network Connections</td>
<td>

- [x] Network connection is observed.
- [ ] The app connects to web services on start.
- [ ] The app checks for update automatically.
- [ ] The app has unnecessary connections.
- [ ] Tracking domains connected.
- [ ] Connections not described clearly in description.
- [ ] Unnecessary in-app webview presents in the app.

</td>
</tr>
<tr>
<td>Language Support</td>
<td>

- [x] The app has English support.

</td>
</tr>
<tr>
<td>Security Scan</td>
<td>

- [ ] All or most vendors on VirusTotal or similar scanning services indicate the app is benign.

</td>
</tr>
</tbody>
</table>

**Notes**

- Cold start makes no connection. After `pm clear`, launched with no cities saved: no socket
  owned by the app's uid appeared within 8 seconds. The same check during a city search does
  show one, so the negative is real and not a broken measurement.
- The only peer observed was `2a03:4000:5b:4f3:888d:7fff:fef0:ba8e` on port 443, which resolves as
  `geocoding-api.open-meteo.com`. Searching "Berlin" and "Paris" returned correct results and
  the forecast view rendered seven models with convergence figures.
- Scanning the dex and resources for URLs turns up only the Open-Meteo hosts (`api`, `archive-api`,
  `geocoding-api`, `marine-api`, `previous-runs-api`), the author's `ko-fi` and `liberapay` links,
  and Android/JetBrains documentation URLs left by the toolchain. No analytics, ads or tracker SDKs.
- Android's own App info screen reports "No permissions required". The app asks for no location
  permission at all and picks cities by search instead, which is a good sign for a weather app.
- The declared `TetheredNet` matches what I saw. All data comes from Open-Meteo and there is no
  in-app way to point it elsewhere.
- Security Scan is left unticked because I did not run VirusTotal, not because anything failed.

Method: connections were read from `/proc/net/tcp{,6}` filtered by the app's uid rather than with
PCAPdroid, so a connection shorter than the poll interval could in principle have been missed.

Disclosure: I am not an F-Droid maintainer, just a developer with an app in this same queue, and
this review is AI-assisted. Every figure above was measured on the device rather than inferred,
but treat it as a second pair of eyes, not an authority.

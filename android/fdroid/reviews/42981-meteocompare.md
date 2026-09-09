### Device / Tester Review

Tested on my phone, Galaxy S21+ / Android 15. Used the apk from this MR's own CI (job 16117122134, `com.meteocompare.app_28.binary.apk`), the signer matches AllowedAPKSigningKeys.

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

- [x] All or most vendors on VirusTotal or similar scanning services indicate the app is benign.

</td>
</tr>
</tbody>
</table>

Notes

- Works. Added Berlin and Paris, got the 7 model comparison with convergence %, the hourly strip, wind and rain. English UI, own icon, nothing in logcat.
- Zero permissions. App info literally says "No permissions required". No location either, you search for a city by name.
- Cold start: cleared data, opened the app, watched its sockets for 15 s (5 samples/s from /proc/net/tcp). Nothing. It only goes online when you search or open a city.
- Everything it talked to during the test, resolved back: geocoding-api, api, marine-api, previous-runs-api, archive-api, all open-meteo.com. Nothing else. The strings in the apk agree: only open-meteo hosts, the dev's ko-fi and liberapay links, and a few android/jetbrains doc urls that come from libraries.
- No trackers, no update check, no webview (0 references to android.webkit.WebView in the dex). The help page is native.
- Settings has a refresh interval, 1 h by default, can be set to manual. That's what the boot/wakelock/foreground service permissions are for (widgets). It's a data refresh, not an update check.
- VirusTotal 0/68: https://www.virustotal.com/gui/file/cc5a93a8be9612ce5752790fe3c34736d0efcd3801859dd40c5bc725cd34530e (7 engines "unable to process file type", the usual for apks).
- TetheredNet as declared fits what I saw.

I don't work on F-Droid, I have my own app waiting in this queue (!48112). Did the testing with AI help, the numbers are from the phone.

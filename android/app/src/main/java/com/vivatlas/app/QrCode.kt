package com.vivatlas.app

/**
 * Reading what the sign-in QR carries.
 *
 * The server draws an ordinary URL — `https://your-vivatlas/qr/<token>` — rather
 * than a bare secret, so one scan says both *which server* and *who*. That is
 * what makes this work on a fresh install: the address never has to be typed
 * either.
 *
 * Anything can end up in front of a camera, so nothing here trusts the scan: a
 * code that isn't one of ours is simply [parse] returning null, and the screen
 * says so rather than posting a stranger's string somewhere.
 */
object QrCode {

    /** A scanned sign-in code, split into the two things it carries. */
    data class Pass(val server: String, val token: String)

    /**
     * Pull the server and the one-time token out of a scanned string, or null if it
     * isn't a VIVATLAS sign-in code.
     *
     * Deliberately strict: http/https only, `/qr/<token>` as the whole path, and a
     * token that looks like the URL-safe random the server issues. A QR from a
     * poster or a payment app fails all three and is refused, not attempted.
     */
    fun parse(raw: String?): Pass? {
        val text = raw?.trim().orEmpty()
        if (text.isEmpty()) return null

        val uri = try {
            android.net.Uri.parse(text)
        } catch (_: Exception) {
            return null
        }

        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host?.takeIf { it.isNotBlank() } ?: return null

        val segments = uri.pathSegments ?: return null
        if (segments.size != 2 || segments[0] != "qr") return null

        val token = segments[1]
        if (!isToken(token)) return null

        // Rebuild the origin rather than trusting the string: whatever else was in
        // the scanned URL (query, fragment, credentials) is dropped, and what we
        // keep is exactly what Prefs stores everywhere else — scheme, host, port.
        //
        // A public host is talked to over https even if the code said http. This is
        // the same rule Prefs applies to a typed address, and it matters more here:
        // the token IS the credential, so sending it in the clear is the one thing
        // this must not do. It also happens to be what makes the sign-in work at
        // all behind a TLS-terminating proxy, which cannot see its own https and
        // may hand out an http code — the app would follow it into a redirect that
        // HttpURLConnection refuses to cross protocols for.
        val safeScheme = if (scheme == "http" && !Prefs.isLocalHost(host)) "https" else scheme
        val port = if (uri.port > 0) ":${uri.port}" else ""
        return Pass(server = "$safeScheme://$host$port", token = token)
    }

    /** The shape of `secrets.token_urlsafe(32)`: URL-safe base64, and long. */
    private fun isToken(s: String): Boolean =
        s.length in 20..200 && s.all { it.isLetterOrDigit() || it == '-' || it == '_' }
}

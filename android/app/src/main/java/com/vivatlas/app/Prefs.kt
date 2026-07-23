package com.vivatlas.app

import android.content.Context

/**
 * One stored value: the base URL of the user's VIVATLAS server (e.g.
 * `http://10.0.2.2:8710` on the emulator, `https://vivatlas.example.com` in real
 * life). Everything else the shell needs — the login, the language — lives in the
 * WebView's own cookies/session, not here.
 */
object Prefs {
    private const val FILE = "vivatlas"
    private const val KEY_SERVER = "server_url"

    fun serverUrl(context: Context): String? =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString(KEY_SERVER, null)
            ?.takeIf { it.isNotBlank() }

    fun setServerUrl(context: Context, url: String) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SERVER, normalize(url))
            .apply()
    }

    /** Trim, add a scheme if the user typed a bare host, drop a trailing slash.
     *  A bare public hostname defaults to https (that's where the session cookie is
     *  Secure); localhost / LAN IPs default to http (typical dev servers). */
    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isEmpty()) return url
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            val host = url.substringBefore('/').substringBefore(':')
            url = (if (isLocalHost(host)) "http://" else "https://") + url
        }
        return url.trimEnd('/')
    }

    private fun isLocalHost(host: String): Boolean {
        return host == "localhost" ||
            host == "10.0.2.2" ||          // emulator -> host
            host.startsWith("127.") ||
            host.startsWith("192.168.") ||
            host.startsWith("10.") ||
            Regex("^172\\.(1[6-9]|2\\d|3[01])\\.").containsMatchIn(host)
    }
}

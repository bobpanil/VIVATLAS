package com.vivatlas.app

import android.content.Context

/**
 * What the app itself remembers: which VIVATLAS to talk to (e.g. `http://10.0.2.2:8710`
 * on the emulator, `https://vivatlas.example.com` in real life), and how a share from
 * another app should be filed. The login and the language stay where they belong — in
 * the WebView's own cookies.
 */
object Prefs {
    private const val FILE = "vivatlas"
    private const val KEY_SERVER = "server_url"
    private const val KEY_SHARE_SHARED = "share_shared"

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

    /**
     * Should a link shared from another app land in the shared catalogue?
     *
     * Private by default, and deliberately: a share is one tap from inside someone
     * else's app, with no chance to think about who will see it. Publishing has to be
     * something you chose once, on purpose, here.
     */
    fun shareShared(context: Context): Boolean =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getBoolean(KEY_SHARE_SHARED, false)

    fun setShareShared(context: Context, shared: Boolean) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_SHARE_SHARED, shared)
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

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

    /** Trim, add a scheme if the user typed a bare host, drop a trailing slash. */
    fun normalize(raw: String): String {
        var url = raw.trim()
        if (url.isEmpty()) return url
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://$url"
        }
        return url.trimEnd('/')
    }
}

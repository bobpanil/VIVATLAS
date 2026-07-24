package com.vivatlas.app

import android.webkit.CookieManager

/**
 * The login session, expressed as the `vivatlas_session` cookie in the WebView's
 * shared cookie jar. The cookie value *is* the raw session token — the native
 * login (Auth) writes it here, the WebView then loads the site already signed in,
 * and ShareActivity reads it back as a Bearer token. Cookies persist to disk, so
 * a login survives the app being closed and reopened.
 */
object Session {
    const val COOKIE = "vivatlas_session"

    /** The current token, or null if there is no (non-empty) session cookie. */
    fun token(server: String): String? {
        val raw = CookieManager.getInstance().getCookie(server) ?: return null
        for (part in raw.split(";")) {
            val kv = part.trim().split("=", limit = 2)
            if (kv.size == 2 && kv[0] == COOKIE) {
                return kv[1].takeIf { it.isNotBlank() }
            }
        }
        return null
    }

    fun has(server: String): Boolean = token(server) != null

    /**
     * Store the token the native login obtained so the WebView is authenticated.
     * Secure only over https (matching the server, which marks the cookie Secure
     * there); on a plain-http LAN dev server it must be sent without it.
     */
    fun set(server: String, token: String) {
        val secure = if (server.startsWith("https://", ignoreCase = true)) "; Secure" else ""
        val cm = CookieManager.getInstance()
        cm.setAcceptCookie(true)
        cm.setCookie(server, "$COOKIE=$token; Path=/$secure")
        cm.flush()
    }

    /** Drop the session cookie (sign-out). Expires it in place rather than wiping
     *  the whole jar, so the language cookie and anything else survive. */
    fun clear(server: String) {
        val secure = if (server.startsWith("https://", ignoreCase = true)) "; Secure" else ""
        val cm = CookieManager.getInstance()
        cm.setCookie(server, "$COOKIE=; Path=/; Max-Age=0$secure")
        cm.flush()
    }
}

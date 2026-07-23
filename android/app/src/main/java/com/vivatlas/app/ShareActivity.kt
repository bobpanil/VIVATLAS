package com.vivatlas.app

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.regex.Pattern

/**
 * The share-sheet target. Reddit / Facebook / Chrome → Share → VIVATLAS lands
 * here. We reuse the WebView's own login cookie as a Bearer token and POST the
 * link straight to `/api/ext/add`, so there is no separate native sign-in.
 *
 * If there is no login yet, we hand the link to [MainActivity] which loads the
 * Add form (the server's login+next flow covers auth) and the share is not lost.
 */
class ShareActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val shared = extractSharedText(intent)
        val server = Prefs.serverUrl(this)

        if (shared.isBlank() || server == null) {
            // Not set up (or nothing usable) — open the app so the user can
            // configure the server / log in, carrying the link along.
            handOffToMain(shared)
            return
        }

        val token = sessionToken(server)
        if (token == null) {
            handOffToMain(shared)
            return
        }

        postCapture(server, token, shared)
    }

    /** Prefer a URL inside the shared text; keep the whole thing as `text`. */
    private fun extractSharedText(intent: Intent): String {
        if (intent.action != Intent.ACTION_SEND) return ""
        return intent.getStringExtra(Intent.EXTRA_TEXT)?.trim().orEmpty()
    }

    private fun firstUrl(text: String): String {
        val m = Pattern.compile("https?://\\S+").matcher(text)
        return if (m.find()) m.group() else ""
    }

    /** Read the `vivatlas_session` cookie the WebView stored at login. */
    private fun sessionToken(server: String): String? {
        val raw = CookieManager.getInstance().getCookie(server) ?: return null
        for (part in raw.split(";")) {
            val kv = part.trim().split("=", limit = 2)
            if (kv.size == 2 && kv[0] == "vivatlas_session") {
                return kv[1].takeIf { it.isNotBlank() }
            }
        }
        return null
    }

    private fun postCapture(server: String, token: String, shared: String) {
        val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT).orEmpty()
        Thread {
            val ok = try {
                val body = JSONObject().apply {
                    put("url", firstUrl(shared))
                    put("title", subject)
                    put("text", shared)
                    put("shared", false)
                }.toString()

                val conn = (URL("$server/api/ext/add").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 15000
                    readTimeout = 15000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("Accept", "application/json")
                    // The cookie value *is* the raw session token; the server
                    // accepts it as a Bearer for cross-context requests.
                    setRequestProperty("Authorization", "Bearer $token")
                }
                OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { it.write(body) }
                val code = conn.responseCode
                conn.disconnect()
                code in 200..299
            } catch (_: Exception) {
                false
            }

            Handler(Looper.getMainLooper()).post {
                val msg = if (ok) R.string.share_added else R.string.share_failed
                Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
                finish()
            }
        }.start()
    }

    private fun handOffToMain(shared: String) {
        val i = Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            if (shared.isNotBlank()) {
                putExtra(MainActivity.EXTRA_SHARE_URL, firstUrl(shared).ifBlank { shared })
            }
        }
        startActivity(i)
        finish()
    }
}

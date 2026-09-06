package com.vivatlas.app

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.CookieManager
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
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
 * Before it saves anything it puts up [R.layout.dialog_share]: the link, and
 * which zone it is about to land in. The zone starts from the standing choice in
 * App settings, but it is on screen and one tap from changing — the same shape
 * as the browser extension (a default, plus a per-capture override). Sharing is
 * one tap from inside someone else's app, so "who will see this" has to be
 * something you can see and answer here, not a setting you have to remember.
 *
 * If there is no login yet, we hand the link to [MainActivity] which loads the
 * Add form (the server's login+next flow covers auth) and the share is not lost.
 */
class ShareActivity : AppCompatActivity() {

    /** Set when Add is tapped: the dialog goes, but the activity has to outlive
     *  it long enough to finish the POST and toast the result. */
    private var submitting = false

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

        askZone(server, token, shared)
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

    /**
     * Show what is about to be saved and where it will land, then save it. The
     * zone chosen here applies to this one share; the standing default stays
     * where it is set, in App settings.
     */
    private fun askZone(server: String, token: String, shared: String) {
        val view = layoutInflater.inflate(R.layout.dialog_share, null)

        // What we're about to save. The sharing app's own title when it gave us
        // one; otherwise the link speaks for itself (and a share with no link at
        // all is text, so show that instead).
        val link = firstUrl(shared)
        val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim().orEmpty()
        val heading = subject.ifBlank { if (link.isBlank()) shared else "" }
        view.findViewById<TextView>(R.id.share_title).apply {
            text = heading
            visibility = if (heading.isBlank()) View.GONE else View.VISIBLE
        }
        view.findViewById<TextView>(R.id.share_url).apply {
            text = link
            visibility = if (link.isBlank()) View.GONE else View.VISIBLE
        }

        val zones = view.findViewById<RadioGroup>(R.id.share_zone)
        val hint = view.findViewById<TextView>(R.id.share_zone_hint)

        fun goShared() = zones.checkedRadioButtonId == R.id.share_zone_shared

        // Say what the current choice actually means, and where the preset came
        // from — so neither the zone nor the default is a guess.
        fun showHint() {
            val meaning = getString(
                if (goShared()) R.string.share_zone_hint_shared
                else R.string.share_zone_hint_private
            )
            hint.text = "$meaning\n${getString(R.string.share_zone_default)}"
        }

        zones.check(
            if (Prefs.shareShared(this)) R.id.share_zone_shared else R.id.share_zone_private
        )
        showHint()
        zones.setOnCheckedChangeListener { _, _ -> showHint() }

        val dialog = AlertDialog.Builder(this).setView(view).create()
        dialog.window?.apply {
            setBackgroundDrawableResource(android.R.color.transparent)
            // Dim the app we were shared from, so the sheet reads as the thing
            // being answered rather than an overlay on someone else's screen.
            setDimAmount(0.6f)
        }
        // Backing out (Back, or a tap outside) means "don't save" — the share is
        // simply dropped, which is what cancelling a share should do.
        dialog.setOnDismissListener { if (!submitting) finish() }

        view.findViewById<TextView>(R.id.share_cancel).setOnClickListener { dialog.dismiss() }
        view.findViewById<TextView>(R.id.share_add).setOnClickListener {
            val toShared = goShared()
            submitting = true
            dialog.dismiss()
            postCapture(server, token, shared, toShared)
        }
        dialog.show()
    }

    private fun postCapture(server: String, token: String, shared: String, toShared: Boolean) {
        val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT).orEmpty()
        Thread {
            val ok = try {
                val body = JSONObject().apply {
                    put("url", firstUrl(shared))
                    put("title", subject)
                    put("text", shared)
                    put("shared", toShared)
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
                // Name the zone on the way out too: the answer to "where did that
                // go" shouldn't need opening the card.
                val msg = when {
                    !ok -> R.string.share_failed
                    toShared -> R.string.share_added_shared
                    else -> R.string.share_added_private
                }
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

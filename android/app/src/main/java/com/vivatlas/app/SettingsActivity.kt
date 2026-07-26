package com.vivatlas.app

import android.os.Bundle
import android.view.View
import android.widget.RadioButton
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

/**
 * The app's own settings — the things that belong to this device rather than to the
 * catalogue: which server it talks to, how a share from another app is filed, and
 * signing out. Everything about the library itself stays in the web UI, where it is
 * the same on every device.
 */
class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        window.statusBarColor = getColor(R.color.login_bg)
        window.navigationBarColor = getColor(R.color.login_bg)

        val server = findViewById<TextView>(R.id.settings_server)
        val private = findViewById<RadioButton>(R.id.zone_private)
        val shared = findViewById<RadioButton>(R.id.zone_shared)

        fun showServer() {
            server.text = Prefs.serverUrl(this) ?: getString(R.string.server_hint)
        }
        showServer()

        // Where a shared link lands. Private is the default and stays the default:
        // sharing is one tap from inside another app, so publishing has to be chosen.
        if (Prefs.shareShared(this)) shared.isChecked = true else private.isChecked = true
        private.setOnClickListener { Prefs.setShareShared(this, false) }
        shared.setOnClickListener { Prefs.setShareShared(this, true) }

        findViewById<View>(R.id.settings_back).setOnClickListener { finish() }

        findViewById<View>(R.id.settings_change_server).setOnClickListener {
            ServerDialog.show(this, Prefs.serverUrl(this), initial = false) { url ->
                Prefs.setServerUrl(this, url)
                showServer()
            }
        }

        findViewById<View>(R.id.settings_signout).setOnClickListener { confirmSignOut() }

        findViewById<TextView>(R.id.settings_version).text =
            getString(R.string.settings_version, BuildConfig.VERSION_NAME)
    }

    /** Signing out drops the session this app holds; the catalogue asks for it again. */
    private fun confirmSignOut() {
        val url = Prefs.serverUrl(this) ?: return
        AlertDialog.Builder(this)
            .setTitle(R.string.settings_signout_confirm)
            .setPositiveButton(R.string.settings_signout) { _, _ ->
                Session.clear(url)
                setResult(RESULT_SIGNED_OUT)
                finish()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    companion object {
        /** Told to MainActivity so it can send the user back to the sign-in screen. */
        const val RESULT_SIGNED_OUT = 101
    }
}

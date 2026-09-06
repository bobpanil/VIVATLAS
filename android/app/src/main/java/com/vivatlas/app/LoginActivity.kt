package com.vivatlas.app

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

/**
 * Native sign-in. Talks to the server's extension API (Auth) and, on success,
 * writes the session cookie (Session) so the WebView in MainActivity loads the
 * catalogue already authenticated. Two steps: email + password, then a 2FA code
 * if the account has it. Finishes RESULT_OK on success; backing out returns
 * cancelled (MainActivity then leaves the app).
 */
class LoginActivity : AppCompatActivity() {

    private lateinit var server: String
    private var ticket: String? = null // set once the server asks for the 2FA step

    private lateinit var email: EditText
    private lateinit var password: EditText
    private lateinit var code: EditText
    private lateinit var useBackup: CheckBox
    private lateinit var error: TextView
    private lateinit var submit: Button
    private lateinit var progress: ProgressBar
    private lateinit var primaryGroup: View
    private lateinit var mfaGroup: View
    private lateinit var scanGroup: View
    private lateinit var title: TextView

    /** The scanner screen. Registered up front (it must be, before onCreate ends)
     *  and only actually opened when the button is tapped. */
    private lateinit var scanner: ActivityResultLauncher<ScanOptions>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = Prefs.serverUrl(this)
        if (url == null) {
            setResult(RESULT_CANCELED)
            finish()
            return
        }
        server = url
        setContentView(R.layout.activity_login)
        // Match the system bars to the warm login canvas so there's no seam at the top.
        window.statusBarColor = getColor(R.color.login_bg)
        window.navigationBarColor = getColor(R.color.login_bg)

        email = findViewById(R.id.email)
        password = findViewById(R.id.password)
        code = findViewById(R.id.code)
        useBackup = findViewById(R.id.use_backup)
        error = findViewById(R.id.error)
        submit = findViewById(R.id.submit)
        progress = findViewById(R.id.progress)
        primaryGroup = findViewById(R.id.primary_group)
        mfaGroup = findViewById(R.id.mfa_group)
        scanGroup = findViewById(R.id.scan_group)
        title = findViewById(R.id.login_title)

        // ZXing asks for the camera itself and hands back the decoded string, or a
        // null one when the user backed out — which is not an error, just a no.
        scanner = registerForActivityResult(ScanContract()) { result ->
            result?.contents?.let { onScanned(it) }
        }

        submit.setOnClickListener { onSubmit() }
        findViewById<View>(R.id.scan).setOnClickListener { startScan() }
        findViewById<TextView>(R.id.forgot).setOnClickListener {
            startActivity(android.content.Intent(this, ResetPasswordActivity::class.java))
        }
        findViewById<TextView>(R.id.change_server).setOnClickListener { changeServer() }
        // Keyboard "Done"/"Go" submits, like tapping the button.
        password.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_DONE || id == EditorInfo.IME_ACTION_GO) { onSubmit(); true } else false
        }
        code.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_DONE || id == EditorInfo.IME_ACTION_GO) { onSubmit(); true } else false
        }

        // Back on the 2FA step returns to email/password; on the first step it
        // leaves the app (default: finish -> RESULT_CANCELED to MainActivity).
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (ticket != null) {
                    resetToPrimary()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })
    }

    /** Open the camera on the code. */
    private fun startScan() {
        clearError()
        if (!packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_CAMERA_ANY)) {
            showError(getString(R.string.login_scan_no_camera))
            return
        }
        val options = ScanOptions()
            .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setPrompt(getString(R.string.login_scan_prompt))
            .setBeepEnabled(false)
            .setOrientationLocked(false)
        scanner.launch(options)
    }

    /**
     * A code came back. It carries the server as well as the pass, so a scan can
     * sign in a fresh install that was never told an address — but only once we are
     * sure it is one of ours: anything else is refused here, unsent.
     */
    private fun onScanned(text: String) {
        val pass = QrCode.parse(text)
        if (pass == null) {
            showError(getString(R.string.login_scan_bad))
            return
        }
        // The code names the server it came from; trust that over what was stored,
        // and remember it, so the WebView and the share target talk to the same one.
        if (pass.server != server) {
            Prefs.setServerUrl(this, pass.server)
            server = Prefs.serverUrl(this) ?: pass.server
        }
        perform { Auth.qr(server, pass.token) }
    }

    private fun onSubmit() {
        clearError()
        val t = ticket
        if (t == null) {
            val e = email.text.toString().trim()
            val p = password.text.toString()
            if (e.isEmpty() || p.isEmpty()) {
                showError(getString(R.string.login_need_fields))
                return
            }
            perform { Auth.login(server, e, p) }
        } else {
            val c = code.text.toString().trim()
            if (c.isEmpty()) {
                showError(getString(R.string.login_need_code))
                return
            }
            perform { Auth.mfa(server, t, c, useBackup.isChecked) }
        }
    }

    /** Run an Auth call off the main thread and apply the result back on it. */
    private fun perform(call: () -> Auth.Result) {
        setBusy(true)
        Thread {
            val result = call()
            Handler(Looper.getMainLooper()).post {
                setBusy(false)
                when (result) {
                    is Auth.Result.Success -> {
                        Session.set(server, result.token)
                        setResult(RESULT_OK)
                        finish()
                    }
                    is Auth.Result.Mfa -> enterMfa(result.ticket)
                    is Auth.Result.Failed ->
                        showError(result.message.ifBlank { getString(R.string.login_failed) })
                }
            }
        }.start()
    }

    private fun enterMfa(t: String) {
        ticket = t
        primaryGroup.visibility = View.GONE
        mfaGroup.visibility = View.VISIBLE
        scanGroup.visibility = View.GONE
        title.setText(R.string.login_mfa_title)
        submit.setText(R.string.login_verify)
        code.text?.clear()
        code.requestFocus()
    }

    private fun resetToPrimary() {
        ticket = null
        clearError()
        mfaGroup.visibility = View.GONE
        primaryGroup.visibility = View.VISIBLE
        scanGroup.visibility = View.VISIBLE
        title.setText(R.string.login_title)
        submit.setText(R.string.login_submit)
    }

    private fun setBusy(busy: Boolean) {
        submit.isEnabled = !busy
        submit.alpha = if (busy) 0.5f else 1f
        progress.visibility = if (busy) View.VISIBLE else View.GONE
    }

    private fun showError(msg: String) {
        error.text = msg
        error.visibility = View.VISIBLE
    }

    private fun clearError() {
        error.visibility = View.GONE
    }

    private fun changeServer() {
        ServerDialog.show(this, server, initial = false) { url ->
            Prefs.setServerUrl(this, url)
            server = url
            resetToPrimary()
        }
    }
}

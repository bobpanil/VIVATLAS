package com.vivatlas.app

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Native password reset — the app's own screen (matching the login), not a
 * WebView. Takes the email, asks the server to send a reset link, and shows a
 * confirmation. The link in the email opens the reset page as usual.
 */
class ResetPasswordActivity : AppCompatActivity() {

    private lateinit var server: String
    private var sent = false

    private lateinit var email: EditText
    private lateinit var submit: Button
    private lateinit var progress: ProgressBar
    private lateinit var error: TextView
    private lateinit var result: TextView
    private lateinit var form: View
    private lateinit var back: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = Prefs.serverUrl(this)
        if (url == null) {
            finish()
            return
        }
        server = url
        setContentView(R.layout.activity_reset)
        window.statusBarColor = getColor(R.color.login_bg)
        window.navigationBarColor = getColor(R.color.login_bg)

        email = findViewById(R.id.reset_email)
        submit = findViewById(R.id.reset_submit)
        progress = findViewById(R.id.reset_progress)
        error = findViewById(R.id.reset_error)
        result = findViewById(R.id.reset_result)
        form = findViewById(R.id.form_group)
        back = findViewById(R.id.reset_back)

        submit.setOnClickListener { if (sent) finish() else send() }
        back.setOnClickListener { finish() }
        email.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_DONE) { send(); true } else false
        }
    }

    private fun send() {
        error.visibility = View.GONE
        val e = email.text.toString().trim()
        if (e.isEmpty()) {
            showError(getString(R.string.reset_need_email))
            return
        }
        setBusy(true)
        Thread {
            val ok = Auth.forgot(server, e)
            Handler(Looper.getMainLooper()).post {
                setBusy(false)
                if (ok) showSent() else showError(getString(R.string.reset_failed))
            }
        }.start()
    }

    /** The request went through — confirm, and turn the button into a way back.
     *  We say "if that email has an account" (never confirming it exists). */
    private fun showSent() {
        sent = true
        form.visibility = View.GONE
        error.visibility = View.GONE
        result.visibility = View.VISIBLE
        back.visibility = View.GONE
        submit.setText(R.string.reset_back_button)
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
}

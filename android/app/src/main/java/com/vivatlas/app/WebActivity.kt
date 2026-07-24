package com.vivatlas.app

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

/**
 * A lightweight in-app browser. The app never sends the user to the system
 * browser — password reset (/forgot) and any external link from the catalogue
 * open here instead, with a back arrow and title. http/https load in place;
 * only genuinely non-web schemes (mailto:, tel:) go to their own app.
 */
class WebActivity : AppCompatActivity() {

    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = intent.getStringExtra(EXTRA_URL)
        if (url.isNullOrBlank()) {
            finish()
            return
        }
        setContentView(R.layout.activity_web)

        web = findViewById(R.id.web_view)
        val title = findViewById<TextView>(R.id.web_title)
        val progress = findViewById<ProgressBar>(R.id.web_progress)
        val fixedTitle = intent.getStringExtra(EXTRA_TITLE)
        title.text = fixedTitle.orEmpty()

        findViewById<View>(R.id.web_back).setOnClickListener {
            onBackPressedDispatcher.onBackPressed()
        }

        CookieManager.getInstance().setAcceptCookie(true)
        web.overScrollMode = View.OVER_SCROLL_NEVER
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            allowFileAccess = false
            allowContentAccess = false
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progress.progress = newProgress
                progress.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }

            override fun onReceivedTitle(view: WebView, t: String?) {
                // Keep a caller-supplied title (e.g. "Reset password"); otherwise
                // reflect the page's own title as an in-app browser would.
                if (fixedTitle.isNullOrBlank() && !t.isNullOrBlank()) title.text = t
            }
        }

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val scheme = request.url.scheme?.lowercase()
                if (scheme == "http" || scheme == "https") return false // keep browsing in-app
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, request.url))
                    true
                } catch (_: Exception) {
                    true
                }
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })

        web.loadUrl(url)
    }

    companion object {
        const val EXTRA_URL = "com.vivatlas.app.WEB_URL"
        const val EXTRA_TITLE = "com.vivatlas.app.WEB_TITLE"

        fun open(activity: android.app.Activity, url: String, title: String? = null) {
            activity.startActivity(
                Intent(activity, WebActivity::class.java).apply {
                    putExtra(EXTRA_URL, url)
                    if (title != null) putExtra(EXTRA_TITLE, title)
                },
            )
        }
    }
}

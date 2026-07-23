package com.vivatlas.app

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

/**
 * The entire browsing UI: a full-screen WebView pointed at the user's VIVATLAS
 * server. The server renders the responsive web UI, so this activity stays
 * deliberately thin — its only jobs are: pick/remember the server URL, keep
 * server-origin navigation inside the WebView (external links go to the system
 * browser), handle downloads, and wire the hardware Back button to history.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var serverUrl: String? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webview)
        configureWebView()
        wireBackButton()

        serverUrl = Prefs.serverUrl(this)
        val url = serverUrl
        if (url == null) {
            promptForServer(initial = true)
        } else {
            loadStart(url, intent)
        }
    }

    /** A second share/launch arriving while we're already open. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        serverUrl?.let { loadStart(it, intent) }
    }

    /**
     * Load either the home page or, when we were handed a pending share (the user
     * shared a link before logging in), the Add form pre-filled with it — the
     * server's own login+next flow takes over if a session is needed.
     */
    private fun loadStart(base: String, intent: Intent?) {
        val pending = intent?.getStringExtra(EXTRA_SHARE_URL)?.takeIf { it.isNotBlank() }
        if (pending != null) {
            intent.removeExtra(EXTRA_SHARE_URL)
            webView.loadUrl("$base/add?source=" + Uri.encode(pending))
        } else if (webView.url == null) {
            webView.loadUrl(base)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)

        val cookies = CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        cookies.setAcceptThirdPartyCookies(webView, true)

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            mediaPlaybackRequiresUserGesture = true
            cacheMode = WebSettings.LOAD_DEFAULT
            // Harden: this is our own trusted origin, but there is no reason to let
            // page JS reach the local filesystem.
            allowFileAccess = false
            allowContentAccess = false
        }

        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean {
                val target = request.url
                val scheme = target.scheme?.lowercase()
                // Keep http(s) on our own host inside the WebView; send everything
                // else (other sites, mailto:, tel:, intent:) to the system.
                if ((scheme == "http" || scheme == "https") && sameHostAsServer(target)) {
                    return false
                }
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, target))
                    true
                } catch (_: Exception) {
                    false
                }
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError,
            ) {
                // Only surface failures of the top-level page, not sub-resources.
                if (request.isForMainFrame) {
                    Toast.makeText(
                        this@MainActivity,
                        getString(R.string.err_unreachable),
                        Toast.LENGTH_LONG,
                    ).show()
                    promptForServer(initial = false)
                }
            }
        }

        // Route downloads (exports, etc.) through the system DownloadManager,
        // carrying the login cookie so authenticated files come through.
        webView.setDownloadListener { url, _, contentDisposition, mimeType, _ ->
            try {
                val req = DownloadManager.Request(Uri.parse(url))
                CookieManager.getInstance().getCookie(url)?.let {
                    req.addRequestHeader("Cookie", it)
                }
                val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
                req.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED,
                )
                req.setDestinationInExternalPublicDir(
                    android.os.Environment.DIRECTORY_DOWNLOADS,
                    name,
                )
                (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
                Toast.makeText(this, getString(R.string.download_started), Toast.LENGTH_SHORT).show()
            } catch (_: Exception) {
                Toast.makeText(this, getString(R.string.download_failed), Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun sameHostAsServer(uri: Uri): Boolean {
        val host = serverUrl?.let { Uri.parse(it).host } ?: return false
        return uri.host.equals(host, ignoreCase = true)
    }

    private fun wireBackButton() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    AlertDialog.Builder(this@MainActivity)
                        .setTitle(R.string.leave_title)
                        .setPositiveButton(R.string.leave_exit) { _, _ -> finish() }
                        .setNeutralButton(R.string.change_server) { _, _ -> promptForServer(false) }
                        .setNegativeButton(android.R.string.cancel, null)
                        .show()
                }
            }
        })
    }

    /** First-run and "change server" dialog. */
    private fun promptForServer(initial: Boolean) {
        val input = EditText(this).apply {
            hint = getString(R.string.server_hint)
            setText(serverUrl ?: getString(R.string.server_default))
            setSelection(text.length)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.server_title)
            .setMessage(R.string.server_message)
            .setView(input)
            .setCancelable(!initial)
            .setPositiveButton(R.string.save) { _, _ ->
                val url = Prefs.normalize(input.text.toString())
                if (url.isNotEmpty()) {
                    Prefs.setServerUrl(this, url)
                    serverUrl = url
                    webView.loadUrl(url)
                }
            }
            .apply { if (!initial) setNegativeButton(android.R.string.cancel, null) }
            .show()
    }

    companion object {
        const val EXTRA_SHARE_URL = "com.vivatlas.app.SHARE_URL"
    }
}

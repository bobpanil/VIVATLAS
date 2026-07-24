package com.vivatlas.app

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * The app frame. A full-screen WebView renders the responsive VIVATLAS web UI,
 * wrapped in native chrome so it reads as an app, not a browser: a cold-start
 * splash, a branded loading screen for the first paint, a slim page-load bar for
 * navigations, and a native offline screen with Retry. Authentication is native
 * too (LoginActivity) — this activity routes to it when there is no session and
 * intercepts a drop back to the web /login (a dead session, or signing out).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var loadingView: View
    private lateinit var errorView: View
    private lateinit var topProgress: ProgressBar
    private var serverUrl: String? = null

    // Splash stays up until the first route decision; then the branded loading
    // overlay (same navy) carries the first page load, so there is no flash.
    private var contentReady = false
    private var firstPaintDone = false
    // When we last returned from native sign-in — used to not re-intercept a
    // just-authenticated load and loop back into the login screen.
    private var lastAuthAt = 0L

    private val loginLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            lastAuthAt = SystemClock.elapsedRealtime()
            firstPaintDone = false
            showLoading()
            serverUrl?.let { loadStart(it, intent, force = true) }
        } else {
            // Backed out of sign-in — leave the app rather than sit on a blank frame.
            finish()
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        splash.setKeepOnScreenCondition { !contentReady }

        webView = findViewById(R.id.webview)
        swipe = findViewById(R.id.swipe)
        loadingView = findViewById(R.id.loading)
        errorView = findViewById(R.id.error_view)
        topProgress = findViewById(R.id.topprogress)

        swipe.setColorSchemeColors(0xFFE7940E.toInt())
        swipe.setOnRefreshListener { webView.reload() }

        findViewById<View>(R.id.retry).setOnClickListener { retry() }
        findViewById<View>(R.id.error_change_server).setOnClickListener { promptForServer(false) }

        configureWebView()
        wireBackButton()

        serverUrl = Prefs.serverUrl(this)
        enter()
    }

    /** A second share/launch arriving while we're already open. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val url = serverUrl ?: return
        if (Session.has(url)) {
            loadStart(url, intent, force = true)
        } else {
            launchLogin()
        }
    }

    /** Decide the first screen: sign-in when there's no session, otherwise the
     *  WebView (a stale cookie is caught later and routed to sign-in). */
    private fun enter() {
        val url = serverUrl
        if (url == null) {
            promptForServer(initial = true)
        } else if (Session.has(url)) {
            showLoading()
            loadStart(url, intent)
        } else {
            launchLogin()
        }
        contentReady = true
    }

    private fun launchLogin() {
        showLoading()
        loginLauncher.launch(Intent(this, LoginActivity::class.java))
    }

    /**
     * Load either the home page or, when handed a pending share (a link shared
     * before sign-in), the Add form pre-filled with it. [force] reloads home even
     * if the WebView already shows a page (used after signing in).
     */
    private fun loadStart(base: String, intent: Intent?, force: Boolean = false) {
        val pending = intent?.getStringExtra(EXTRA_SHARE_URL)?.takeIf { it.isNotBlank() }
        if (pending != null) {
            intent.removeExtra(EXTRA_SHARE_URL)
            webView.loadUrl("$base/add?source=" + Uri.encode(pending))
        } else if (force || webView.url == null) {
            webView.loadUrl(base)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        // No blue edge glow — a browser tell; pull-to-refresh is the SwipeRefreshLayout.
        webView.overScrollMode = View.OVER_SCROLL_NEVER

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

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                topProgress.progress = newProgress
                topProgress.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean {
                val target = request.url
                val scheme = target.scheme?.lowercase()
                if (scheme == "http" || scheme == "https") {
                    if (sameHostAsServer(target)) return false       // stay in the app's own WebView
                    WebActivity.open(this@MainActivity, target.toString()) // external -> in-app, never a browser
                    return true
                }
                // mailto:, tel:, geo:, intent: — the matching app, not a browser.
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, target))
                    true
                } catch (_: Exception) {
                    true
                }
            }

            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                // A drop to the web /login means the session ended (expired, or the
                // user signed out) — take over with the native sign-in instead.
                if (isAuthPage(url) && !recentlyAuthed()) {
                    view.stopLoading()
                    serverUrl?.let { Session.clear(it) }
                    launchLogin()
                }
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError,
            ) {
                // Only the top-level page, and not a page we deliberately stopped
                // (the /login takeover), should raise the offline screen.
                if (request.isForMainFrame && !isAuthPage(request.url.toString())) {
                    swipe.isRefreshing = false
                    showError()
                }
            }

            override fun onPageFinished(view: WebView, url: String) {
                swipe.isRefreshing = false
                topProgress.visibility = View.GONE
                if (!isAuthPage(url)) {
                    firstPaintDone = true
                    hideLoading()
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

    private fun isAuthPage(url: String): Boolean {
        val path = Uri.parse(url).path ?: return false
        return path == "/login" || path.startsWith("/login/")
    }

    private fun recentlyAuthed(): Boolean = SystemClock.elapsedRealtime() - lastAuthAt < 4000L

    private fun sameHostAsServer(uri: Uri): Boolean {
        val host = serverUrl?.let { Uri.parse(it).host } ?: return false
        return uri.host.equals(host, ignoreCase = true)
    }

    private fun showLoading() {
        errorView.visibility = View.GONE
        loadingView.visibility = View.VISIBLE
    }

    private fun hideLoading() {
        loadingView.visibility = View.GONE
    }

    private fun showError() {
        loadingView.visibility = View.GONE
        topProgress.visibility = View.GONE
        errorView.visibility = View.VISIBLE
    }

    private fun retry() {
        firstPaintDone = false
        showLoading()
        val base = serverUrl
        if (webView.url != null) webView.reload() else if (base != null) webView.loadUrl(base)
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

    /** First-run and "change server" dialog. On save we re-route through [enter],
     *  which will ask for sign-in if the new server has no session yet. */
    private fun promptForServer(initial: Boolean) {
        ServerDialog.show(this, serverUrl, initial) { url ->
            Prefs.setServerUrl(this, url)
            serverUrl = url
            firstPaintDone = false
            enter()
        }
    }

    companion object {
        const val EXTRA_SHARE_URL = "com.vivatlas.app.SHARE_URL"
    }
}

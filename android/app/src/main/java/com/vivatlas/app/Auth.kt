package com.vivatlas.app

import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Native sign-in against the server's extension JSON API — the same endpoints the
 * browser extension uses. `/api/ext/login` takes email + password and returns
 * either a session token (which is also the cookie value) or, for a 2FA account,
 * a short-lived ticket to exchange at `/api/ext/mfa` with the code. Every call
 * blocks on the network — run them off the main thread.
 */
object Auth {

    sealed class Result {
        data class Success(val token: String, val name: String) : Result()
        data class Mfa(val ticket: String) : Result()
        /** [message] is the server's own message when it gave one, else empty
         *  (a network failure) — the caller picks a generic string for empty. */
        data class Failed(val message: String) : Result()
    }

    fun login(server: String, email: String, password: String): Result =
        call("$server/api/ext/login", JSONObject().put("email", email).put("password", password))

    fun mfa(server: String, ticket: String, code: String, backup: Boolean): Result =
        call(
            "$server/api/ext/mfa",
            JSONObject().put("ticket", ticket).put("code", code).put("backup", backup),
        )

    /**
     * Ask the server to email a password-reset link. It answers the same whether
     * or not the address has an account (no enumeration), so a reachable server
     * means the request was accepted — returns true. false = network failure.
     */
    fun forgot(server: String, email: String): Boolean {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL("$server/forgot").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15000
                readTimeout = 20000
                doOutput = true
                setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
                setRequestProperty("Accept", "text/html")
            }
            val body = "email=" + java.net.URLEncoder.encode(email, "UTF-8")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            conn.responseCode in 200..399
        } catch (_: Exception) {
            false
        } finally {
            conn?.disconnect()
        }
    }

    private fun call(url: String, body: JSONObject): Result {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15000
                readTimeout = 20000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Accept", "application/json")
            }
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            parse(code, text)
        } catch (_: Exception) {
            Result.Failed("")
        } finally {
            conn?.disconnect()
        }
    }

    private fun parse(code: Int, text: String): Result {
        val json = try {
            JSONObject(text)
        } catch (_: Exception) {
            return Result.Failed("")
        }
        if (code !in 200..299 || !json.optBoolean("ok", false)) {
            return Result.Failed(json.optString("error", ""))
        }
        if (json.optBoolean("mfa_required", false)) {
            return Result.Mfa(json.optString("ticket"))
        }
        val token = json.optString("token")
        if (token.isBlank()) return Result.Failed("")
        val name = json.optJSONObject("user")?.optString("name").orEmpty()
        return Result.Success(token, name)
    }
}

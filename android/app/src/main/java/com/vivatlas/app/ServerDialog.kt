package com.vivatlas.app

import android.app.Activity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AlertDialog

/**
 * The branded "VIVATLAS server" dialog — an ivory card with a styled field and a
 * gold Save, matching the login (the stock AlertDialog looked a decade old).
 * Shared by the login's "Change server" and the frame's first-run prompt.
 */
object ServerDialog {
    fun show(activity: Activity, current: String?, initial: Boolean, onSave: (String) -> Unit) {
        val view = activity.layoutInflater.inflate(R.layout.dialog_server, null)
        val input = view.findViewById<EditText>(R.id.server_input)
        input.setText(current ?: activity.getString(R.string.server_default))
        input.setSelection(input.text.length)

        val dialog = AlertDialog.Builder(activity)
            .setView(view)
            .setCancelable(!initial)
            .create()
        dialog.window?.apply {
            setBackgroundDrawableResource(android.R.color.transparent)
            // Darken the login card behind it so the two ivory surfaces don't
            // muddle together — the dialog reads as clearly on top.
            setDimAmount(0.6f)
        }

        val cancel = view.findViewById<TextView>(R.id.server_cancel)
        cancel.visibility = if (initial) View.GONE else View.VISIBLE
        cancel.setOnClickListener { dialog.dismiss() }

        fun save() {
            val url = Prefs.normalize(input.text.toString())
            if (url.isNotEmpty()) {
                onSave(url)
                dialog.dismiss()
            }
        }
        view.findViewById<TextView>(R.id.server_save).setOnClickListener { save() }
        input.setOnEditorActionListener { _, id, _ ->
            if (id == EditorInfo.IME_ACTION_DONE) { save(); true } else false
        }
        dialog.show()
    }
}

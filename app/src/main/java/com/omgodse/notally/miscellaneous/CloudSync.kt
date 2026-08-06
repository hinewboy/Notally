package com.omgodse.notally.miscellaneous

import android.app.Application
import android.content.Context
import android.os.Handler
import android.os.Looper
import com.omgodse.notally.room.*
import com.omgodse.notally.room.dao.BaseNoteDao
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * 云同步客户端：注册 / 登录 / 上传 / 下载笔记。
 * 使用 HttpURLConnection + org.json，零第三方依赖。
 */
object CloudSync {

    const val ServerBase = "https://notebook.978021.xyz"
    const val PrefToken = "cloud_token"
    const val PrefUsername = "cloud_username"

    class ApiException(message: String, val code: Int = -1) : Exception(message)

    /** 令牌与用户名持久化 */
    fun saveSession(context: Context, token: String, username: String) {
        val sp = context.getSharedPreferences("cloud_session", Context.MODE_PRIVATE)
        sp.edit().putString(PrefToken, token).putString(PrefUsername, username).apply()
    }

    fun getSession(context: Context): Pair<String?, String?> {
        val sp = context.getSharedPreferences("cloud_session", Context.MODE_PRIVATE)
        return sp.getString(PrefToken, null) to sp.getString(PrefUsername, null)
    }

    fun clearSession(context: Context) {
        val sp = context.getSharedPreferences("cloud_session", Context.MODE_PRIVATE)
        sp.edit().clear().apply()
    }

    suspend fun register(username: String, password: String): Pair<String, String> = withContext(Dispatchers.IO) {
        val body = JSONObject().put("username", username).put("password", password).toString()
        request("/api/register", "POST", body).let { parseSession(it) }
    }

    suspend fun login(username: String, password: String): Pair<String, String> = withContext(Dispatchers.IO) {
        val body = JSONObject().put("username", username).put("password", password).toString()
        request("/api/login", "POST", body).let { parseSession(it) }
    }

    /** 上传全部笔记（含图片/音频附件），返回服务器确认的笔记数 */
    suspend fun upload(token: String, notes: List<BaseNote>, context: Context): Int = withContext(Dispatchers.IO) {
        val array = JSONArray()
        notes.forEach { array.put(noteToJson(it)) }

        // 收集附件（图片/音频）base64
        val attachments = JSONArray()
        val app = context.applicationContext as Application
        val imageDir = IO.getExternalImagesDirectory(app)
        val audioDir = IO.getExternalAudioDirectory(app)
        notes.forEach { note ->
            note.images.forEach { image ->
                val file = if (imageDir != null) File(imageDir, image.name) else null
                if (file != null && file.exists()) {
                    val data = android.util.Base64.encodeToString(file.readBytes(), android.util.Base64.NO_WRAP)
                    attachments.put(JSONObject().apply {
                        put("noteId", note.id)
                        put("kind", "image")
                        put("name", image.name)
                        put("mime", image.mimeType)
                        put("data", data)
                    })
                }
            }
            note.audios.forEach { audio ->
                val file = if (audioDir != null) File(audioDir, audio.name) else null
                if (file != null && file.exists()) {
                    val data = android.util.Base64.encodeToString(file.readBytes(), android.util.Base64.NO_WRAP)
                    attachments.put(JSONObject().apply {
                        put("noteId", note.id)
                        put("kind", "audio")
                        put("name", audio.name)
                        put("mime", "audio/mp4")
                        put("data", data)
                    })
                }
            }
        }

        val body = JSONObject().put("notes", array).put("attachments", attachments).toString()
        val response = request("/api/notes", "PUT", body, token)
        response.optInt("count", 0)
    }

    /** 下载云端全部笔记 */
    suspend fun download(token: String): List<BaseNote> = withContext(Dispatchers.IO) {
        val response = request("/api/notes", "GET", null, token)
        val array = response.optJSONArray("notes") ?: JSONArray()
        (0 until array.length()).map { jsonToNote(array.getJSONObject(it)) }
    }

    private fun parseSession(json: JSONObject): Pair<String, String> {
        val token = json.optString("token")
        val username = json.optString("username")
        if (token.isEmpty()) throw ApiException(json.optString("message", "未知错误"))
        return token to username
    }

    private fun request(path: String, method: String, body: String?, token: String? = null): JSONObject {
        val connection = URL(ServerBase + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 15000
            connection.readTimeout = 30000
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
            if (token != null) connection.setRequestProperty("Authorization", "Bearer $token")

            if (body != null) {
                val output: OutputStream = connection.outputStream
                output.write(body.toByteArray(Charsets.UTF_8))
                output.flush()
                output.close()
            }

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""

            if (code !in 200..299) {
                val message = try {
                    JSONObject(text).optString("message", "HTTP $code")
                } catch (_: Exception) {
                    "HTTP $code"
                }
                throw ApiException(message, code)
            }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    /** BaseNote → JSON（仅文本内容，不含图片/音频二进制） */
    private fun noteToJson(note: BaseNote): JSONObject = JSONObject().apply {
        put("id", note.id)
        put("type", note.type.name)
        put("folder", note.folder.name)
        put("color", note.color.name)
        put("title", note.title)
        put("pinned", note.pinned)
        put("timestamp", note.timestamp)
        put("labels", JSONArray(note.labels))
        put("body", note.body)
        put("spans", Converters.spansToJSONArray(note.spans))
        put("items", Converters.itemsToJSONArray(note.items))
        put("images", Converters.imagesToJson(note.images))
        put("audios", Converters.audiosToJson(note.audios))
        put("reminder", note.reminder?.let { JSONObject().put("timestamp", it.timestamp).put("frequency", it.frequency.name) } ?: JSONObject.NULL)
    }

    /** JSON → BaseNote */
    private fun jsonToNote(json: JSONObject): BaseNote {
        val id = json.optLong("id", 0)
        val type = Type.valueOf(json.optString("type", "NOTE"))
        val folder = Folder.valueOf(json.optString("folder", "NOTES"))
        val color = Color.valueOf(json.optString("color", "DEFAULT"))
        val title = json.optString("title")
        val pinned = json.optBoolean("pinned", false)
        val timestamp = json.optLong("timestamp", System.currentTimeMillis())
        val labels = (json.optJSONArray("labels") ?: JSONArray()).toStringList()
        val body = json.optString("body")

        val spansJson = json.optJSONArray("spans") ?: JSONArray()
        val spans = (0 until spansJson.length()).map { i ->
            val o = spansJson.getJSONObject(i)
            SpanRepresentation(
                bold = o.optBoolean("bold"), link = o.optBoolean("link"), italic = o.optBoolean("italic"),
                monospace = o.optBoolean("monospace"), strikethrough = o.optBoolean("strikethrough"),
                start = o.optInt("start"), end = o.optInt("end")
            )
        }

        val itemsJson = json.optJSONArray("items") ?: JSONArray()
        val items = (0 until itemsJson.length()).map { i ->
            val o = itemsJson.getJSONObject(i)
            ListItem(o.optString("body"), o.optBoolean("checked"))
        }

        var reminder: Reminder? = null
        if (!json.isNull("reminder")) {
            val o = json.getJSONObject("reminder")
            val freqName = o.optString("frequency", "ONCE")
            val frequency = try {
                Frequency.valueOf(freqName)
            } catch (_: Exception) {
                Frequency.ONCE
            }
            reminder = Reminder(o.optLong("timestamp"), frequency)
        }

        return BaseNote(
            id = id, type = type, folder = folder, color = color, title = title,
            pinned = pinned, timestamp = timestamp, labels = labels, body = body,
            spans = spans, items = items, images = emptyList(), audios = emptyList(), reminder = reminder
        )
    }

    /** JSON 中的 images/audios 字段（附件名列表） */
    fun parseImages(json: JSONObject): List<String> {
        val arr = json.optJSONArray("images") ?: JSONArray()
        return (0 until arr.length()).map { i ->
            arr.getJSONObject(i).optString("name")
        }
    }

    fun parseAudios(json: JSONObject): List<String> {
        val arr = json.optJSONArray("audios") ?: JSONArray()
        return (0 until arr.length()).map { i ->
            arr.getJSONObject(i).optString("name")
        }
    }

    private fun JSONArray.toStringList(): List<String> = (0 until length()).map { getString(it) }

    /** 通过 DAO 将下载的笔记写入本地（删除回收站/归档后全量替换 NOTES） */
    suspend fun applyDownload(dao: BaseNoteDao, notes: List<BaseNote>) {
        dao.deleteFrom(Folder.NOTES)
        dao.deleteFrom(Folder.ARCHIVED)
        notes.forEach { dao.insert(it) }
    }

    /** 简单的密码 SHA-256（用于本地显示，不用于认证） */
    fun sha256(input: String): String {
        val bytes = MessageDigest.getInstance("SHA-256").digest(input.toByteArray(Charsets.UTF_8))
        return bytes.joinToString("") { "%02x".format(it) }
    }

    /** 主线程 toast 辅助 */
    fun toast(context: Context, message: String) {
        Handler(Looper.getMainLooper()).post {
            android.widget.Toast.makeText(context, message, android.widget.Toast.LENGTH_SHORT).show()
        }
    }
}

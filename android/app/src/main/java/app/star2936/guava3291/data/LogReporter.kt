package app.star2936.guava3291.data

import android.content.Context
import android.util.Log
import app.star2936.guava3291.BuildConfig
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/**
 * App 日志 / 崩溃上报：
 * - 全局未捕获异常（崩溃）→ 先落盘本地文件，再尝试立即上报；
 *   崩溃后网络来不及的场景，由下次启动补报（flushAll）。
 * - 支持主动记录 error/warn/info 日志，同样落盘后批量上报。
 * - 上报接口：POST /api/log-report（服务器写入 app_logs 表，管理端可查）。
 */
object LogReporter {

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val executor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "log-reporter").apply { isDaemon = true }
    }

    @Volatile private var appContext: Context? = null
    @Volatile private var prevHandler: Thread.UncaughtExceptionHandler? = null

    /** 初始化：包装默认崩溃处理器 + 启动补报上次未上行的日志 */
    fun init(context: Context) {
        appContext = context.applicationContext
        prevHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val stack = Log.getStackTraceString(throwable)
                persist("crash", "uncaught", throwable.message ?: throwable.javaClass.simpleName, stack)
                // 异步尽力上报（不阻塞崩溃线程）；失败靠下次启动补报
                executor.execute { flushAll() }
            } catch (_: Exception) {
            }
            // 交还系统默认行为（崩溃框 / 闪退），保持 App 原有行为
            prevHandler?.uncaughtException(thread, throwable)
        }
        executor.execute { flushAll() }
    }

    /** 主动记录一条日志（error/warn/info） */
    fun log(level: String, tag: String, message: String, throwable: Throwable? = null) {
        try {
            val stack = throwable?.let { Log.getStackTraceString(it) } ?: ""
            persist(level, tag, message, stack)
            executor.execute { flushAll() }
        } catch (_: Exception) {
        }
    }

    private fun logsDir(): File? {
        val ctx = appContext ?: return null
        val dir = File(ctx.filesDir, "logs")
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    private fun persist(level: String, tag: String, message: String, stack: String) {
        val dir = logsDir() ?: return
        val name = "log_${System.currentTimeMillis()}.txt"
        val sb = StringBuilder()
        sb.append("TIME=").append(System.currentTimeMillis()).append('\n')
        sb.append("LEVEL=").append(level).append('\n')
        sb.append("TAG=").append(tag).append('\n')
        sb.append("MSG=").append(message).append('\n')
        sb.append("STACK=").append(stack).append('\n')
        File(dir, name).writeText(sb.toString())
    }

    /** 批量补报本地未上行的日志；网络失败即停，留给下次 */
    private fun flushAll() {
        val dir = logsDir() ?: return
        val files = dir.listFiles()?.sortedBy { it.name } ?: return
        for (f in files) {
            try {
                val content = f.readText()
                val level = valueOf(content, "LEVEL=") ?: "info"
                val tag = valueOf(content, "TAG=") ?: ""
                val msg = valueOf(content, "MSG=") ?: ""
                val stack = content.substringAfter("STACK=", "")
                if (upload(level, tag, msg, stack)) {
                    f.delete()
                } else {
                    break // 网络不可用，停止本轮，留下次启动补报
                }
            } catch (_: Exception) {
                f.delete() // 文件损坏直接丢弃，避免死循环
            }
        }
    }

    private fun valueOf(content: String, key: String): String? {
        val idx = content.indexOf(key)
        if (idx < 0) return null
        val start = idx + key.length
        val end = content.indexOf('\n', start)
        return if (end < 0) content.substring(start) else content.substring(start, end)
    }

    /** 单条上报：POST /api/log-report，成功返回 true */
    private fun upload(level: String, tag: String, message: String, stack: String): Boolean {
        return try {
            val ctx = appContext ?: return false
            val json = JSONObject().apply {
                put("platform", DeviceInfo.platform)
                // 带 versionCode：同版本名不同构建（1.1.20 的 20/21）可精确区分，服务器据此确认用户是否装到最新包
                put("app_version", "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
                put("device_id", DeviceInfo.deviceId)
                put("level", level.take(8))
                put("tag", tag.take(64))
                put("message", message.take(8192))
                put("stack", stack.take(8192))
            }.toString()
            val req = Request.Builder()
                // BASE 结尾无斜杠，必须补 "/" 否则拼成 apilog-report 404
                .url("${Api.BASE}/log-report")
                .post(json.toRequestBody(jsonMedia))
                .build()
            val resp = Api.http.newCall(req).execute()
            val ok = resp.code in 200..299
            resp.close()
            ok
        } catch (_: Exception) {
            false
        }
    }
}

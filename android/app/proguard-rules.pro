# ============================================================
# 流萤音乐 Android 发布包 ProGuard/R8 规则（工业级加固）
# 原则：混淆业务代码，但保留运行时依赖的类/属性/注解。
# ============================================================

# ---------- 通用：保留注解与签名元数据（kotlinx.serialization/反射依赖） ----------
-keepattributes *Annotation*, InnerClasses, Signature, EnclosingMethod, Exceptions
-keepattributes SourceFile,LineNumberTable   # 保留行号便于崩溃定位（release 崩溃追踪必需）

# ---------- 数据模型层（JSON 反序列化依赖字段名，全部保留） ----------
-keep class app.star2936.guava3291.data.** { *; }

# ---------- kotlinx.serialization（序列化器由编译器生成，需保留） ----------
-keep,includedescriptorclasses class app.star2936.guava3291.**$$serializer { *; }
-keepclassmembers class app.star2936.guava3291.** {
    *** Companion;
    *** Companion$*;
}
-keepclasseswithmembers class app.star2936.guava3291.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class kotlinx.serialization.** { *; }
-keepclassmembers class kotlinx.serialization.** { *** Companion; }
-keep,includedescriptorclasses class kotlinx.serialization.** { *; }
-dontwarn kotlinx.serialization.**

# ---------- AndroidManifest 组件（四大组件/入口不能被混淆） ----------
-keep public class * extends android.app.Application
-keep public class * extends android.app.Activity
-keep public class * extends android.app.Service
-keep public class * extends android.content.BroadcastReceiver
-keep public class * extends android.content.ContentProvider

# ---------- 安全加固组件（Obfuscator/ApkGuard 保持可追踪，防二次打包逻辑不被裁掉） ----------
-keep class app.star2936.guava3291.security.** { *; }

# ---------- 媒体3 / ExoPlayer（自带 consumer 规则，补充保护） ----------
-keep class androidx.media3.** { *; }
-dontwarn androidx.media3.**

# ---------- OkHttp（自带规则，补充 keep） ----------
-keep class okhttp3.** { *; }
-keep interface okhttp3.** { *; }
-dontwarn okhttp3.**
-dontwarn okio.**

# ---------- Compose（编译器生成的强引用由插件规则覆盖，补充） ----------
-dontwarn androidx.compose.**
-keepclassmembers class androidx.compose.** { *; }

# ---------- Kotlin 协程/标准库 ----------
-dontwarn kotlinx.coroutines.**
-dontwarn org.jetbrains.annotations.**
-dontwarn kotlin.**

# ---------- 通知/系统服务等反射调用 ----------
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# ---------- 状态层（AppRouter 等被跨模块引用，保留以防混淆引用错乱） ----------
-keep class app.star2936.guava3291.state.** { *; }

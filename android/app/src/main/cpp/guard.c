// ============================================================
// 流萤音乐 native 防护层（libguard.so）
// 把反调试 / 注入框架检测 / 字符串解密下沉到 so 层：
//   - dex 中不再含 XOR 密钥与明文敏感串，核心校验不在 Java 层可见；
//   - 反调试：TracerPid 检测（标准、无误报）；
//   - 注入框架检测：/proc/self/maps 特征（frida/xposed/substrate/zygisk）；
//   - 字符串解密：XOR 还原（与 Java 侧 Obfuscator 同 key，密文同源）。
// 仅使用 TracerPid/maps 这类零误报检测，不做 ptrace(TRACEME) 自检，
// 避免 seccomp 严格设备误杀正常用户。
// ============================================================
#include <jni.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

static const unsigned char XOR_KEY = 0x5A;

// TracerPid 非 0 = 被调试器/附加工具跟踪
static int tracer_pid(void) {
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return 0;
    char line[256];
    int pid = 0;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "TracerPid:", 10) == 0) {
            pid = atoi(line + 10);
            break;
        }
    }
    fclose(f);
    return pid;
}

JNIEXPORT jboolean JNICALL
Java_app_star2936_guava3291_security_NativeGuard_isDebugged(JNIEnv *env, jclass clazz) {
    return tracer_pid() != 0 ? JNI_TRUE : JNI_FALSE;
}

// /proc/self/maps 中检索注入框架特征
JNIEXPORT jboolean JNICALL
Java_app_star2936_guava3291_security_NativeGuard_hasHookFramework(JNIEnv *env, jclass clazz) {
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f) return JNI_FALSE;
    char line[1024];
    const char *keywords[] = {
        "frida", "gadget", "xposed", "substrate", "riru", "zygisk", "magisk", NULL
    };
    while (fgets(line, sizeof(line), f)) {
        for (int i = 0; keywords[i]; i++) {
            if (strstr(line, keywords[i])) {
                fclose(f);
                return JNI_TRUE;
            }
        }
    }
    fclose(f);
    return JNI_FALSE;
}

// hex 密文 → XOR 还原明文（密钥仅存在于 so 二进制中）
JNIEXPORT jstring JNICALL
Java_app_star2936_guava3291_security_NativeGuard_decrypt(JNIEnv *env, jclass clazz, jstring hex) {
    const char *in = (*env)->GetStringUTFChars(env, hex, NULL);
    if (!in) return NULL;
    size_t len = strlen(in);
    char *out = (char *)malloc(len / 2 + 1);
    if (!out) {
        (*env)->ReleaseStringUTFChars(env, hex, in);
        return NULL;
    }
    size_t oi = 0;
    for (size_t i = 0; i + 1 < len; i += 2) {
        int b = 0;
        char hi = in[i], lo = in[i + 1];
        if (hi >= '0' && hi <= '9') b |= (hi - '0') << 4;
        else if (hi >= 'a' && hi <= 'f') b |= (hi - 'a' + 10) << 4;
        if (lo >= '0' && lo <= '9') b |= (lo - '0');
        else if (lo >= 'a' && lo <= 'f') b |= (lo - 'a' + 10);
        out[oi++] = (char)((unsigned char)b ^ XOR_KEY);
    }
    out[oi] = '\0';
    (*env)->ReleaseStringUTFChars(env, hex, in);
    jstring ret = (*env)->NewStringUTF(env, out);
    free(out);
    return ret;
}

package app.star2936.guava3291.state

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * 可视化效果设置：波形进度条、音频频谱、音乐DNA等开关。
 * 默认全部关闭，用户在设置页手动开启。
 */
object VisualizationSettings {
    private const val PREFS_NAME = "visualization_prefs"
    private const val KEY_WAVEFORM_BAR = "waveform_progress_bar"
    private const val KEY_AUDIO_SPECTRUM = "audio_spectrum_bg"
    private const val KEY_MUSIC_DNA = "music_dna_effect"

    private lateinit var prefs: SharedPreferences

    // 状态流（UI 观察）
    val waveformBarEnabled = MutableStateFlow(false)
    val audioSpectrumEnabled = MutableStateFlow(false)
    val musicDnaEnabled = MutableStateFlow(false)

    fun init(context: Context) {
        prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        waveformBarEnabled.value = prefs.getBoolean(KEY_WAVEFORM_BAR, false)
        audioSpectrumEnabled.value = prefs.getBoolean(KEY_AUDIO_SPECTRUM, false)
        musicDnaEnabled.value = prefs.getBoolean(KEY_MUSIC_DNA, false)
    }

    fun setWaveformBar(enabled: Boolean) {
        waveformBarEnabled.value = enabled
        prefs.edit().putBoolean(KEY_WAVEFORM_BAR, enabled).apply()
    }

    fun setAudioSpectrum(enabled: Boolean) {
        audioSpectrumEnabled.value = enabled
        prefs.edit().putBoolean(KEY_AUDIO_SPECTRUM, enabled).apply()
    }

    fun setMusicDna(enabled: Boolean) {
        musicDnaEnabled.value = enabled
        prefs.edit().putBoolean(KEY_MUSIC_DNA, enabled).apply()
    }
}

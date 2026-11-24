/**
 * Voice input (microphone → transcription)
 * ★ The testid prefix is not here — the caller's testIdPrefix decides it.
 */
export default {
  svcDown: "语音服务未连接 · 后台正在自动重连",
  noAudio: "没录到声音",
  noText: "这段没听出字",
  tooLarge: "这段太长了，超过 20MB",
  asrUnavailable: "语音服务没连上（本机 8888 未启动？）",
  asrTimeout: "语音服务超时，稍后重试",
  asrFailed: "识别失败，换个说法再试一次",
  transcribeFailed: "转写失败",
  micDenied: "浏览器拒绝了麦克风权限（地址栏那把锁里放行）",
  micNotFound: "没找到麦克风设备",
  micOpenFailed: "打不开麦克风：{{reason}}",
  unknownReason: "未知原因",
  unsupported: "这个浏览器不支持录音",
  recording: "录音中 {{time}}",
  stopTitle: "结束并转成文字",
  done: "完成",
  cancelTitle: "取消这段录音",
  startTitle: "点一下开始说话，再点一下结束（最长 {{sec}} 秒）",
  asking: "等麦克风…",
  transcribing: "转文字中…",
  label: "语音",
} as const;

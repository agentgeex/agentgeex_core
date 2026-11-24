/**
 * Voice input (microphone → transcription)
 * The testid prefix is not here — the caller decides it via testIdPrefix.
 */
export default {
  svcDown: "Speech service not connected · reconnecting in the background",
  noAudio: "No audio was captured",
  noText: "No words recognised in this clip",
  tooLarge: "This clip is too long — over 20MB",
  asrUnavailable: "Speech service unreachable (is 8888 running locally?)",
  asrTimeout: "Speech service timed out — try again shortly",
  asrFailed: "Recognition failed — try saying it differently",
  transcribeFailed: "Transcription failed",
  micDenied: "The browser blocked the microphone (allow it from the lock icon in the address bar)",
  micNotFound: "No microphone found",
  micOpenFailed: "Cannot open the microphone: {{reason}}",
  unknownReason: "unknown reason",
  unsupported: "This browser cannot record audio",
  recording: "Recording {{time}}",
  stopTitle: "Stop and transcribe",
  done: "Done",
  cancelTitle: "Discard this recording",
  startTitle: "Click to start talking, click again to stop (up to {{sec}}s)",
  asking: "Waiting for the microphone…",
  transcribing: "Transcribing…",
  label: "Voice",
} as const;

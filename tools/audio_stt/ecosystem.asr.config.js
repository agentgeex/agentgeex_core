// pm2 config: ASR speech-to-text service (Qwen3-ASR-1.7B)
//
// Why an ecosystem file instead of a pm2 start command line:
//   QWEN_ASR_PYTHON must be given explicitly (the venv is not on the script's auto-detect paths),
//   and pm2 save / resurrect only keep the env defined in the app entry.
//   Values passed via --update-env reach dump.pm2, but are unauditable and invisible to whoever takes over.
//
// Why not asr_service.sh:
//   its resolve_python() hard-requires `import funasr`, which venv-qwen-asr does not have
//   (Qwen3-ASR uses the qwen_asr package). Using it fails outright with "ASR_PYTHON=... cannot import funasr".
//   start_qwen_asr.sh was written for this service and probes for qwen_asr + websockets.
//
// Falling back to Fun-ASR-Nano / SenseVoice:
//   pm2 delete agx-asr
//   pm2 start /home/owner/.agx/asr_service.sh --name agx-asr --interpreter bash -- run
//   (asr_service.sh reads asr.env, whose ASR_ENTRY decides which entry file to use)

module.exports = {
  apps: [
    {
      name: 'agx-asr',
      script: '/home/owner/.agx/tools/audio_stt/start_qwen_asr.sh',
      args: 'run',
      interpreter: 'bash',
      cwd: '/home/owner/.agx/tools/audio_stt',

      // * Key point: the venv is not on start_qwen_asr.sh's auto-detect list (it only looks at $HOME/venv-qwen-asr etc.),
      //   while this venv actually lives under /home/mat/robertfeng. Set it explicitly to remove detection uncertainty.
      env: {
        QWEN_ASR_PYTHON: '/home/mat/robertfeng/venv-qwen-asr/bin/python',
        QWEN_ASR_MODEL: '/home/mat/robertfeng/models/Qwen3-ASR-1.7B',
        ASR_HOST: '0.0.0.0',
        ASR_PORT: '18888',
        // Digit mapping is off by default (laofeng, 2026-11-20: the mapped output was not a better experience).
        // To enable: change to '1'; it goes through cn_num_normalize.py in the same directory.
        QWEN_ASR_ITN_POST: '0',
        // Debug audio is off by default (the original SenseVoice service once accumulated 178MB)
        ASR_DEBUG_AUDIO: '0',
      },

      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      // Model loading takes about 6 seconds; allow enough time before checking liveness
      min_uptime: 30000,

      error_file: '/home/owner/.agx/logs/asr_pm2.err.log',
      out_file: '/home/owner/.agx/logs/asr_pm2.out.log',
      merge_logs: true,
      time: true,
    },
  ],
};

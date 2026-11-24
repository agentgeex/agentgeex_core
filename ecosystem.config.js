// PM2 进程清单 —— 让 ASR 服务随机器自启、挂了自动拉起。
//
// ★ 为什么用 PM2 而不是再写一个 systemd 单元
//   这台机器上 daemon/gateway 已经走 systemd --user 了。ASR 是「一个 python
//   前台进程 + 一份 env 配置」，用 PM2 管更轻：改了配置 `pm2 restart` 即可，
//   不用碰 unit 文件权限、不用 sudo。
//
// ★ 关键：script 指到 asr_service.sh 的 run 子命令，不直接指 python
//   因为配置的真相在 asr.env（三层分层：命令行 > 本机 asr.env > 仓库 asr.env）。
//   绕过脚本直接跑 python，PM2 里的进程就等于用另一套配置 —— 这类「两个入口
//   两套配置」的坑今天已经踩过一次（sensevoice_* 删掉就是为此）。
//   run 会自己 source 那两份 env 再 exec python，单一真相。
//
// ★ run 里是 exec，不是直接调用
//   PM2 停服务发 SIGTERM 给主进程。如果不是 exec，bash 先死、python 变孤儿，
//   端口 8888 和 1.2G 显存都留着不放 —— 下次起来就撞端口。
//
// 用法（在仓库根目录）：
//   pm2 start ecosystem.config.js
//   pm2 save                     # 记住当前清单，开机由 resurrect 恢复
//   pm2 startup systemd -u owner --hp /home/owner   # 只做一次
//   pm2 logs agx-asr
//   pm2 restart agx-asr      # 改完 asr.env 后

module.exports = {
  apps: [
    {
      name: 'agx-asr',
      script: './asr_service.sh',
      args: 'run',
      interpreter: 'bash',
      cwd: __dirname,

      // 崩溃重启。max_restarts 给上限，避免配置错误时无限重启刷日志。
      autorestart: true,
      max_restarts: 20,
      restart_delay: 3000,
      // 模型加载/退出都要时间，杀太快会留下半初始化的进程
      kill_timeout: 15000,
      // 首次转写要加载模型（约 5s），别把这个当成启动失败
      min_uptime: 20000,

      // 日志落到仓库 logs/（与 asr_service.sh 的 start 模式同一目录，好找）
      out_file: './logs/asr_pm2.out.log',
      error_file: './logs/asr_pm2.err.log',
      merge_logs: true,
      time: true,
    },
  ],
};

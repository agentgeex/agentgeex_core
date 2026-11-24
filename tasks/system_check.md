---
reply_mode: silent
---

# 系统检查任务 - System Health Check

## 概述
检查当前系统是否适配 AgentGeeX 的运行环境，确保所有依赖和配置满足最低要求。

## 检查清单

### 1. 硬件资源
- CPU 核数 ≥ 2
- 可用内存 ≥ 4GB
- 磁盘剩余空间 ≥ 20GB
- 网络连通性正常

### 2. 软件依赖
- Python 版本 ≥ 3.10
- Node.js 版本 ≥ 18 (如需)
- MySQL 服务正常运行
- Redis 服务正常运行

### 3. AgentGeeX 配置
- `env.sh` 配置完整（DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME）
- Redis 连接密码配置正确
- 必要的 MCP 服务已启动

### 4. 安全审计
- 防火墙规则未阻断必要端口
- SSH 密钥/密码认证正常
- 文件系统权限正确

## 输出格式
返回 JSON 字符串，包含每项检查的通过/失败状态及错误信息。

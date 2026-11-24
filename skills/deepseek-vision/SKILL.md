---
name: deepseek-vision
description: DeepSeek vision 图片解析备份方案。当 kimi-k3 图片模型过载/超时/不可用时，用 deepseek-v4-flash-vision-exp 描述图片、识别截图文字(OCR)、分析图表/受力图/几何图。支持 JPEG/PNG/GIF/WebP。
author: fxc_math_paper
date_added: '2026-08-22'
tags:
- vision
- image
- ocr
- fallback
- deepseek
tools:
- python
- exec
---

# DeepSeek Vision —— 图片解析备份

## 何时使用
当 OpenClaw 内置图片工具（`image`，走 kimi-k3）返回以下错误时，改用本 skill：
- `429 The engine is currently overloaded`（kimi vision 过载）
- `image description timed out`（kimi 超时）
- 任何 kimi 图片模型不可用的情况

## 原理
DeepSeek 官方提供的图片理解模型 `deepseek-flash`，走标准的
OpenAI 兼容对话补全接口 `https://api.deepseek.com/chat/completions`。
图片以 `data: URL`（Base64 内联）方式传入，见 DeepSeek 文档。

## 文件
- `vision.py` —— 主脚本，纯标准库（urllib），无需安装 openai/requests
- `credentials.txt` —— （可选）存放 API Key，脚本会自动读取；**不要提交到 git**

## 配置 API Key（用户填入）
在 `~/my-project/skills/deepseek-vision/credentials.txt`
写入一行你的 DeepSeek key：
```
sk-你的key
```
或者用环境变量：
```bash
export DEEPSEEK_API_KEY=sk-你的key
```
> ⚠️ `credentials.txt` 含密钥，务必加入 `.gitignore`，勿提交版本库。

## 用法（项目 uv 环境）
```bash
cd ~/my-project

# 描述图片
uv run python skills/deepseek-vision/vision.py papers/zhaozhou/output/Figure_2.png

# 带提问
uv run python skills/deepseek-vision/vision.py papers/zhaozhou/output/赵州桥_几何_极简.png \
  --prompt "描述红色箭头的位置和方向，以及黑色尺寸标注符号"

# OCR 截图文字
uv run python skills/deepseek-vision/vision.py papers/shared/WechatIMG5343.jpg \
  --prompt "提取图中全部文字"

# 显式传 key
uv run python skills/deepseek-vision/vision.py x.png --key sk-xxx
```

## 工作流（Agent 使用）
1. 先试内置 `image` 工具（kimi-k3）
2. 失败（429/超时）→ 用本脚本作为备份解析
3. 图片在工作区时，先 `cp` 到项目目录再调用（脚本可访问项目路径）
4. 把结果整合进回复

## 限制
- 请求体 ≤ 48 MiB，脚本限制单张 ≤ 20 MiB
- 超大图建议先压缩（可接 matplotlib/PIL 缩小）
- 若 401 → key 无效；429 → DeepSeek 服务过载；超时 → 服务慢，稍后重试

#!/usr/bin/env python3
"""
deepseek-vision -- parse images with the DeepSeek vision model (backup for kimi-k3)

Features:
- Describe image content
- Recognize / extract text from screenshots (OCR)
- Analyze charts / math figures / force diagrams, etc.
- Supports JPEG / PNG / GIF / WebP

API key sources (in priority order):
  1. The --key command-line argument
  2. The DEEPSEEK_API_KEY environment variable
  3. credentials.txt next to this file (as sk-xxxxxxxx or DEEPSEEK_API_KEY=sk-xxx)

Usage:
  python vision.py IMAGE_PATH [--prompt "what to ask"] [--key sk-xxx]
  python vision.py Figure_2.png
  python vision.py force-diagram.png --prompt "describe the direction and position of the red arrows"
  python vision.py screenshot.png --prompt "extract all the text in the image"

Dependencies: Python 3.9+, standard library only (urllib); no openai/requests needed.
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

API_BASE = "https://api.deepseek.com"
MODEL = "deepseek-flash"
# The request body counts toward the 48 MiB limit, so cap each image at < 20 MiB
MAX_BYTES = 20 * 1024 * 1024


def load_api_key(cli_key: str | None) -> str:
    """Resolve the key by priority. Give clear guidance when none is found."""
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env
    # Fall back to credentials.txt in the same directory
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("credentials.txt", "../credentials.txt"):
        p = os.path.join(here, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("DEEPSEEK_API_KEY="):
                        return line.split("=", 1)[1].strip()
                    if line.startswith("sk-"):
                        return line
    sys.exit(
        "❌ 找不到 DeepSeek API Key。\n"
        "请任选一种方式提供：\n"
        "  1) 命令行: --key sk-xxxx\n"
        "  2) 环境变量: export DEEPSEEK_API_KEY=sk-xxxx\n"
        "  3) 在本脚本同目录创建 credentials.txt，内容写一行: sk-xxxx"
    )


def guess_mime(path: str) -> str:
    """Determine the MIME type from the file's actual content, not from its name."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    # Fallback: guess by file extension
    ext = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


def encode_image(path: str) -> str:
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        sys.exit(f"❌ 图片 {size/1024/1024:.1f} MiB 超过 20 MiB 上限，请压缩后重试。")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{guess_mime(path)};base64,{b64}"


def call_api(key: str, image_url: str, prompt: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }
    req = urllib.request.Request(
        API_BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        sys.exit(f"❌ HTTP {e.code}: {body[:800]}")
    except urllib.error.URLError as e:
        sys.exit(f"❌ 网络错误: {e.reason}")


def main():
    ap = argparse.ArgumentParser(description="DeepSeek vision 图片解析")
    ap.add_argument("image", help="图片路径 (png/jpg/gif/webp)")
    ap.add_argument("--prompt", default="请详细描述这张图片的内容。",
                    help="对图片的提问 / 指令")
    ap.add_argument("--key", default=None, help="DeepSeek API Key（可选）")
    ap.add_argument("--no-b64", action="store_true",
                    help="（预留）改用其他方式传图，暂未实现")
    args = ap.parse_args()

    path = args.image
    if not os.path.exists(path):
        sys.exit(f"❌ 文件不存在: {path}")
    if not os.path.isfile(path):
        sys.exit(f"❌ 不是文件: {path}")

    key = load_api_key(args.key)
    print(f"📤 上传图片: {path} ({os.path.getsize(path)/1024:.0f} KB)", file=sys.stderr)
    image_url = encode_image(path)
    print(f"🤖 调用模型: {MODEL}", file=sys.stderr)
    result = call_api(key, image_url, args.prompt)
    print(result)


if __name__ == "__main__":
    main()

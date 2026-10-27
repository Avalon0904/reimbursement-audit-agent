#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视觉通道探针：验证 Kimi 各候选模型的图像输入能力。
踩坑预防（项目一记录）：.env 必须 override=True（系统注入 KIMI_BASE_URL 会压过项目配置）。
"""
import base64, json, os, sys
from pathlib import Path
import requests

ENV = os.environ.get("AUDIT_CONFIG_FILE", str(Path(__file__).resolve().parent.parent / "config.local"))
IMG = Path(__file__).parent.parent / "eval" / "dataset" / "images" / "INV-A01.jpg"

def load_env(path):
    """手写 override=True 的 dotenv（避免依赖 python-dotenv）。"""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()  # 直接覆盖系统变量 = override

def probe(base, key, model, img_b64):
    r = requests.post(
        base.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "temperature": 0.6,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "这是一张发票图片。只回答一个数字：价税合计金额是多少元？"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]}],
        },
        timeout=90,
    )
    return r

def main():
    load_env(ENV)
    base, key = os.environ["KIMI_BASE_URL"], os.environ["KIMI_API_KEY"]
    img_b64 = base64.b64encode(IMG.read_bytes()).decode()
    for model in ["kimi-latest", "moonshot-v1-8k-vision-preview", "moonshot-v1-32k-vision-preview"]:
        try:
            r = probe(base, key, model, img_b64)
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"]["content"]
                print(f"[OK] {model}: {msg[:120]}")
                return model
            print(f"[{r.status_code}] {model}: {r.text[:200]}")
        except Exception as e:
            print(f"[ERR] {model}: {e}")
    sys.exit(1)

if __name__ == "__main__":
    main()

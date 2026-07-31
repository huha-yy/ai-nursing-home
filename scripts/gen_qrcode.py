#!/usr/bin/env python3
"""Generate a permanent QR code for a nursing home deployment.

Usage:
    python3 gen_qrcode.py hz-sanfu.eldcare.cn           # save as qrcode-hz-sanfu.png
    python3 gen_qrcode.py hz-shefuli.eldcare.cn         # save as qrcode-hz-shefuli.png
"""

import sys

try:
    import qrcode
except ImportError:
    print("pip install qrcode[pil]")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <domain> [output_path]")
        sys.exit(1)

    domain = sys.argv[1]
    url = f"https://{domain}"
    label = domain.split(".")[0]
    out = sys.argv[2] if len(sys.argv) > 2 else f"qrcode-{label}.png"

    img = qrcode.make(url)
    img.save(out)
    print(f"QR code saved: {out} ({img.size[0]}×{img.size[1]})")
    print(f"URL: {url}")
    print()
    print("Print and post in nurse station / office.")
    print("Bottom text: 请连接院内员工 Wi-Fi 后扫码访问")


if __name__ == "__main__":
    main()

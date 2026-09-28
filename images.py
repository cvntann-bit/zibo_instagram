"""Fotoğrafı Instagram'a uygun hale getirir ve geçici public URL üretir.

Instagram Graph API fotoğrafı dosya olarak kabul etmez, internetten indirebileceği
bir JPEG linki ister. Bu modül:
  1) Fotoğrafı JPEG'e çevirir, en-boy oranını Instagram sınırına (4:5 ile 1.91:1) kırpar,
  2) imgbb'ye yükleyip link alır (link IMGBB_EXPIRATION saniye sonra kendiliğinden silinir).
Kendi sunucun/bucket'ın varsa IMAGE_HOST=static ve PUBLIC_BASE_URL kullan.
"""
import base64
import io
import os

import requests
from PIL import Image, ImageOps

try:  # iPhone HEIC fotoğrafları için
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

MIN_RATIO, MAX_RATIO = 4 / 5, 1.91  # genişlik / yükseklik
MAX_WIDTH = 1440


def prepare_jpeg(path: str) -> bytes:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = img.size
    ratio = w / h
    if ratio < MIN_RATIO:  # fazla dikey → üstten/alttan kırp
        new_h = int(w / MIN_RATIO)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    elif ratio > MAX_RATIO:  # fazla yatay → yanlardan kırp
        new_w = int(h * MAX_RATIO)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, int(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def _upload_imgbb(data: bytes, name: str) -> str:
    key = os.getenv("IMGBB_API_KEY")
    if not key:
        raise RuntimeError("IMGBB_API_KEY eksik (api.imgbb.com adresinden ücretsiz alınır)")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        params={"key": key, "expiration": os.getenv("IMGBB_EXPIRATION", "86400"), "name": name},
        data={"image": base64.b64encode(data).decode()},
        timeout=120,
    )
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"imgbb yükleme hatası: {body}")
    return body["data"]["url"]


def public_url(path: str, rel_name: str) -> str:
    """path: yerel dosya, rel_name: posts/ içindeki göreli ad (static mod için)."""
    host = os.getenv("IMAGE_HOST", "imgbb")
    if host == "static":
        base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if not base:
            raise RuntimeError("IMAGE_HOST=static için PUBLIC_BASE_URL gerekli")
        return f"{base}/{rel_name}"
    stem = os.path.splitext(os.path.basename(path))[0]
    return _upload_imgbb(prepare_jpeg(path), stem)

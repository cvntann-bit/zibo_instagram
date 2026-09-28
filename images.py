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
import time
from urllib.parse import quote

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


def _upload_catbox(data: bytes, name: str, temporary: bool) -> str:
    """Anahtar gerektirmez. temporary=True -> litterbox (24 saat sonra silinir)."""
    if temporary:
        url, extra = "https://litterbox.catbox.moe/resources/internals/api.php", {"time": "24h"}
    else:
        url, extra = "https://catbox.moe/user/api.php", {}
    last = ""
    for attempt in range(3):  # bu siteler ara sıra 500 veriyor, birkaç kez dene
        try:
            resp = requests.post(url, data={"reqtype": "fileupload", **extra},
                                 files={"fileToUpload": (name + ".jpg", data, "image/jpeg")},
                                 headers={"User-Agent": "ZiboBot/1.0"}, timeout=120)
            link = resp.text.strip()
            if resp.status_code == 200 and link.startswith("https://"):
                return link
            last = f"HTTP {resp.status_code} {link[:120]}"
        except requests.RequestException as e:
            last = str(e)[:120]
        time.sleep(5)
    raise RuntimeError(f"catbox yükleme hatası: {last}")


def _upload_uguu(data: bytes, name: str) -> str:
    """Anahtar gerektirmez, dosya birkaç saat sonra silinir."""
    resp = requests.post("https://uguu.se/upload", files={"files[]": (name + ".jpg", data, "image/jpeg")},
                         headers={"User-Agent": "ZiboBot/1.0"}, timeout=120)
    try:
        return resp.json()["files"][0]["url"]
    except Exception:
        raise RuntimeError(f"uguu yükleme hatası: HTTP {resp.status_code} {resp.text[:120]}")


def _github_raw(path: str, rel_name: str) -> str:
    """Depo herkese açıksa (public) fotoğraf doğrudan GitHub'dan verilir. En güvenilir yol."""
    repo = os.getenv("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GitHub dışında çalışıyor")
    img = Image.open(path)
    w, h = img.size
    if img.format != "JPEG" or not (MIN_RATIO <= w / h <= MAX_RATIO) or w > MAX_WIDTH:
        raise RuntimeError("fotoğraf Instagram ölçüsünde değil, başka siteye yüklenecek")
    ref = os.getenv("GITHUB_SHA") or "main"
    posts_dir = os.getenv("POSTS_DIR", "posts").strip("/")
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{quote(posts_dir + '/' + rel_name)}"
    if requests.head(url, timeout=30).status_code != 200:
        raise RuntimeError("depo gizli (private), GitHub linki kullanılamıyor")
    return url


# Instagram bazı sitelerden fotoğrafı indiremiyor (ör. i.ibb.co). Sırayla denenir.
def hosts() -> list:
    raw = os.getenv("IMAGE_HOSTS") or os.getenv("IMAGE_HOST") or "github,litterbox,uguu,catbox,imgbb"
    return [h.strip() for h in raw.split(",") if h.strip()]


def public_url(path: str, rel_name: str, host: str = None) -> str:
    """path: yerel dosya, rel_name: posts/ içindeki göreli ad (static mod için)."""
    host = host or hosts()[0]
    if host == "static":
        base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if not base:
            raise RuntimeError("IMAGE_HOST=static için PUBLIC_BASE_URL gerekli")
        return f"{base}/{rel_name}"
    stem = os.path.splitext(os.path.basename(path))[0]
    if host == "github":
        return _github_raw(path, rel_name)
    if host == "uguu":
        return _upload_uguu(prepare_jpeg(path), stem)
    if host in ("litterbox", "catbox"):
        return _upload_catbox(prepare_jpeg(path), stem, temporary=(host == "litterbox"))
    if host == "imgbb":
        return _upload_imgbb(prepare_jpeg(path), stem)
    raise RuntimeError(f"Bilinmeyen IMAGE_HOST: {host}")

"""Zibo (@zibo.app) fotoğraf paylaşım botu.

1) posts/ klasörünü tarar:
     posts/yeni-kostum.jpg       -> tek fotoğraflık gönderi
     posts/kostum-serisi/*.jpg     -> klasör = kaydırmalı (carousel) gönderi, dosya adı sırasıyla
     posts/yeni-kostum.txt / posts/klasor/not.txt  -> (isteğe bağlı) Claude'a ipucu
2) Yeni bulduklarını Claude ile açıklama + hashtag üreterek posts.json'a ekler
   (bu dosyayı paylaşımdan önce istediğin gibi düzenleyebilirsin).
3) SLOTS saatlerinde kuyruktaki sıradaki gönderiyi paylaşır; publish_at verilen
   gönderi tam o saatte gider. Sonuçlar state.json'da tutulur, tekrar paylaşılmaz.

Kullanım:
    python main.py              # sürekli çalışır
    python main.py --scan       # sadece tara + açıklama üret, paylaşma
    python main.py --once       # kuyruktaki sıradakini hemen paylaş
    python main.py --dry-run    # API'lere dokunmadan ne yapacağını göster
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# Anahtarlarda kopyala-yapıştırdan kalan boşluk/satır sonu olursa API "Connection error" verir; temizle.
for _k in ("ANTHROPIC_API_KEY", "IMGBB_API_KEY", "IG_ACCESS_TOKEN", "IG_USER_ID"):
    if os.environ.get(_k):
        _v = "".join(os.environ[_k].split())
        if _v.startswith(_k + "="):  # "ANTHROPIC_API_KEY=sk-..." diye satırın tamamı yapıştırıldıysa
            _v = _v[len(_k) + 1:]
        os.environ[_k] = _v

POSTS_DIR = os.getenv("POSTS_DIR", "posts")
POSTS_JSON = os.getenv("POSTS_JSON", "posts.json")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Istanbul"))
def parse_slots(raw: str) -> list:
    """'20:00,02:00', '20.00 02.00', '20;2', '20:00, 2:30' gibi yazımları kabul eder."""
    import re as _re
    out = []
    for part in _re.split(r"[,\s;]+", (raw or "").strip()):
        if not part:
            continue
        m = _re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?", part)
        if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
            raise ValueError(f"Geçersiz saat: '{part}' (örnek: 20:00,02:00)")
        out.append(f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}")
    return out


try:
    SLOTS = parse_slots(os.getenv("SLOTS", "12:00,19:00"))
except ValueError:
    SLOTS = ["12:00", "19:00"]
SLOT_GRACE_MIN = int(os.getenv("SLOT_GRACE_MIN", "30"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
DEFAULT_PLATFORMS = [p.strip() for p in os.getenv("DEFAULT_PLATFORMS", "instagram").split(",") if p.strip()]
AUTO_APPROVE = os.getenv("AUTO_APPROVE", "1") == "1"  # 0 ise üretilen açıklamayı onaylamadan paylaşmaz
CHECK_EVERY_SEC = 30

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")],
)
log = logging.getLogger("bot")


# ---------------- dosyalar ----------------
def _read_json(path: str, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_posts() -> list:
    return _read_json(POSTS_JSON, [])


def load_state() -> dict:
    return _read_json(STATE_FILE, {"posts": {}, "fired_slots": []})


def save_state(state: dict) -> None:
    state["fired_slots"] = state["fired_slots"][-200:]
    _write_json(STATE_FILE, state)


def is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXT


def item_files(item: dict) -> list:
    return item.get("files") or [item["file"]]


def build_caption(item: dict, platform: str) -> str:
    text = item.get(f"caption_{platform}", item.get("caption", ""))
    fixed = [t.strip().lstrip("#") for t in os.getenv("FIXED_HASHTAGS", "ziboapp").split(",") if t.strip()]
    gen = [t.lstrip("#") for t in item.get("hashtags", []) if t.lstrip("#") not in fixed]
    tags = " ".join("#" + t for t in fixed + gen)
    return f"{text}\n\n{tags}".strip() if tags else text


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


# ---------------- tarama + açıklama üretimi ----------------
def discover() -> list:
    """posts/ içindeki gönderileri (id, files, not) listeler."""
    found = []
    if not os.path.isdir(POSTS_DIR):
        return found
    for name in sorted(os.listdir(POSTS_DIR)):
        full = os.path.join(POSTS_DIR, name)
        if os.path.isfile(full) and is_image(name):
            stem = os.path.splitext(name)[0]
            note_path = os.path.join(POSTS_DIR, stem + ".txt")
            found.append({"id": name, "files": [name], "note_path": note_path})
        elif os.path.isdir(full):
            imgs = sorted(f for f in os.listdir(full) if is_image(f))[:10]
            if imgs:
                found.append({"id": name + "/", "files": [f"{name}/{f}" for f in imgs],
                              "note_path": os.path.join(full, "not.txt")})
    return found


def _read_note(path: str) -> str:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def scan(dry_run: bool, max_captions: int = 0) -> None:
    """max_captions > 0 ise bu çalıştırmada en fazla o kadar açıklama üretir
    (GitHub Actions'ta 150 postu tek seferde yazıp süreyi aşmamak için)."""
    posts = load_posts()
    by_id = {p["id"]: p for p in posts}
    changed = False
    made = tried = 0

    for found in discover():
        item = by_id.get(found["id"])
        if item is None:
            item = {"id": found["id"], "files": found["files"], "caption": "", "hashtags": [],
                    "platforms": DEFAULT_PLATFORMS, "approved": AUTO_APPROVE}
            posts.append(item)
            by_id[item["id"]] = item
            changed = True
            log.info("Yeni gönderi bulundu: %s (%d foto)", item["id"], len(item["files"]))

        if item.get("caption"):
            continue
        if max_captions and (made >= max_captions or tried >= max_captions + 2):
            continue
        tried += 1
        note = item.get("note") or _read_note(found["note_path"])
        if dry_run:
            log.info("[DRY] %s için açıklama üretilecek (not: %s)", item["id"], note or "-")
            continue
        try:
            import captioner
            gen = captioner.generate([os.path.join(POSTS_DIR, f) for f in item_files(item)], note)
            item["caption"], item["hashtags"] = gen["caption"], gen["hashtags"]
            item.pop("caption_error", None)
            changed = True
            made += 1
            _write_json(POSTS_JSON, posts)  # her açıklamadan sonra kaydet, iş yarıda kesilse de kaybolmasın
            log.info("Açıklama üretildi: %s -> %s", item["id"], gen["caption"][:60])
        except Exception as e:
            item["caption_error"] = str(e)[:300]
            changed = True
            log.error("Açıklama üretilemedi (%s): %s", item["id"], e)

    if changed and not dry_run:
        _write_json(POSTS_JSON, posts)


# ---------------- kuyruk mantığı ----------------
def ready(item: dict) -> bool:
    files_ok = all(os.path.isfile(os.path.join(POSTS_DIR, f)) for f in item_files(item))
    return bool(item.get("caption")) and item.get("approved", True) and files_ok


def pending_platforms(item: dict, state: dict) -> list:
    done = state["posts"].get(item["id"], {})
    return [p for p in item.get("platforms", DEFAULT_PLATFORMS)
            if done.get(p, {}).get("status") != "done"
            and done.get(p, {}).get("attempts", 0) < MAX_ATTEMPTS]


def next_queue_item(posts: list, state: dict):
    for item in posts:
        if not item.get("publish_at") and ready(item) and pending_platforms(item, state):
            return item
    return None


def due_slot(now: datetime, state: dict):
    for slot in SLOTS:
        h, m = map(int, slot.split(":"))
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = slot_dt.strftime("%Y-%m-%d %H:%M")
        if slot_dt <= now < slot_dt + timedelta(minutes=SLOT_GRACE_MIN) and key not in state["fired_slots"]:
            return key
    return None


# ---------------- paylaşım ----------------
def publish_to(platform: str, item: dict, dry_run: bool) -> str:
    files = item_files(item)
    caption = build_caption(item, platform)
    if dry_run:
        log.info("[DRY] %s <- %s | %s", platform, ", ".join(files), caption[:70].replace("\n", " "))
        return "dry-run"

    import images
    urls = [images.public_url(os.path.join(POSTS_DIR, f), f) for f in files]

    if platform == "instagram":
        from instagram import InstagramClient
        ig = InstagramClient(os.getenv("IG_USER_ID", ""), os.environ["IG_ACCESS_TOKEN"],
                             os.getenv("IG_API_VERSION", "v23.0"))
        return ig.publish_carousel(urls, caption) if len(urls) > 1 else ig.publish_image(urls[0], caption)

    if platform == "tiktok":
        if os.getenv("IMAGE_HOST", "imgbb") != "static":
            raise RuntimeError("TikTok fotoğrafı yalnızca doğrulanmış kendi domaininden çeker "
                               "(IMAGE_HOST=static + PUBLIC_BASE_URL)")
        from tiktok import TikTokClient
        tt = TikTokClient(os.environ["TIKTOK_CLIENT_KEY"], os.environ["TIKTOK_CLIENT_SECRET"],
                          privacy_level=os.getenv("TIKTOK_PRIVACY", "SELF_ONLY"))
        return tt.publish_photos(urls, caption)

    raise RuntimeError(f"Bilinmeyen platform: {platform}")


def post_item(item: dict, state: dict, dry_run: bool) -> None:
    rec = state["posts"].setdefault(item["id"], {})
    for p in pending_platforms(item, state):
        if dry_run:
            publish_to(p, item, True)
            continue
        entry = rec.setdefault(p, {"attempts": 0})
        try:
            media_id = publish_to(p, item, False)
            entry.update(status="done", media_id=media_id, at=datetime.now(TZ).isoformat())
            log.info("OK   %s -> %s (%s)", item["id"], p, media_id)
        except Exception as e:  # bir platform hata verirse diğeri yine denenir
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry.update(status="failed", error=str(e)[:500])
            log.error("HATA %s -> %s (deneme %s/%s): %s", item["id"], p, entry["attempts"], MAX_ATTEMPTS, e)
        save_state(state)


def tick(state: dict, dry_run: bool) -> None:
    scan(dry_run)
    posts = load_posts()
    now = datetime.now(TZ)

    for item in posts:
        if item.get("publish_at") and parse_time(item["publish_at"]) <= now \
                and ready(item) and pending_platforms(item, state):
            log.info("Zamanı gelen gönderi: %s", item["id"])
            post_item(item, state, dry_run)

    key = due_slot(now, state)
    if key:
        if not dry_run:
            state["fired_slots"].append(key)
            save_state(state)
        item = next_queue_item(posts, state)
        if item:
            log.info("Slot %s -> %s", key, item["id"])
            post_item(item, state, dry_run)
        else:
            log.info("Slot %s: kuyrukta hazır gönderi yok", key)


def status(state: dict) -> None:
    for item in load_posts():
        done = state["posts"].get(item["id"], {})
        marks = ", ".join(f"{p}:{done.get(p, {}).get('status', 'bekliyor')}"
                          for p in item.get("platforms", DEFAULT_PLATFORMS))
        flag = "" if ready(item) else "  (hazır değil: açıklama/onay/dosya)"
        log.info("%-25s %s%s", item["id"], marks, flag)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = args.dry_run or os.getenv("DRY_RUN") == "1"
    state = load_state()
    if not dry and not os.path.exists(STATE_FILE):
        save_state(state)  # GitHub'daki "git add posts.json state.json" ikisi de var olmazsa hiçbir şey kaydetmiyor

    if args.status:
        return status(state)
    if args.scan:
        return scan(dry)
    if args.once:
        scan(dry, max_captions=1)  # sadece sıradaki post için açıklama yaz
        item = next_queue_item(load_posts(), state)
        if not item:
            log.info("Kuyrukta hazır gönderi yok")
            return 0
        post_item(item, state, dry)
        failed = [p for p, r in state["posts"].get(item["id"], {}).items() if r.get("status") == "failed"]
        return 1 if failed and not dry else 0  # GitHub Actions hata e-postası atsın

    log.info("Bot başladı | slotlar: %s | %s", ", ".join(SLOTS), "DRY-RUN" if dry else "CANLI")
    while True:
        try:
            tick(state, dry)
        except Exception as e:
            log.exception("Döngü hatası: %s", e)
        time.sleep(CHECK_EVERY_SEC)


if __name__ == "__main__":
    sys.exit(main())

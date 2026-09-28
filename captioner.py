"""Fotoğraftan açıklama + hashtag üretir (Claude API, görsel anlama).

Fotoğraf(lar) küçültülüp Claude'a gönderilir, dönen JSON:
    {"caption": "...", "hashtags": ["...", ...]}
Ton ve kurallar .env'deki CAPTION_BRIEF ile belirlenir.
"""
import base64
import io
import json
import os
import re

from PIL import Image, ImageOps

try:  # iPhone HEIC fotoğrafları için
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

DEFAULT_BRIEF = (
    "Account: @zibo.app — Zibo is a chubby, cute 3D cartoon mascot and the user's 'digital buddy' "
    "in a mobile app (Google Play) for goal tracking, habits, gratitude, mood and money/savings. "
    "Voice: Zibo himself talking — warm, supportive, playful, motivating friend; never preachy or corporate. "
    "Language: English."
)

PROMPT = """Write an Instagram photo caption and hashtags.

Account and voice:
{brief}

Rules:
- Base it on what is actually visible in the image(s); don't invent places, people or events.
- Caption: 1-3 short sentences, at most 2 emojis. You may end with a short question or a soft
  call to action (e.g. "link in bio") — not on every post.
- {n_tags} hashtags: a few broad (motivation, habits, selfcare...), most specific to the content;
  no "#", no spaces, lowercase. Don't include: {fixed}.
- {extra}
Return ONLY this JSON, nothing else:
{{"caption": "...", "hashtags": ["...", "..."]}}"""


def _encode(path: str, max_side: int = 1568) -> dict:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg",
                   "data": base64.standard_b64encode(buf.getvalue()).decode()},
    }


def _parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"Model JSON döndürmedi: {text[:200]}")
    data = json.loads(match.group(0))
    tags = [re.sub(r"[^\w]", "", t.lstrip("#").lower()) for t in data.get("hashtags", [])]
    return {"caption": data.get("caption", "").strip(), "hashtags": [t for t in tags if t]}


def generate(image_paths: list, note: str = "") -> dict:
    """image_paths: tek fotoğraf ya da carousel fotoğrafları (ilk 5'i gönderilir).
    note: isteğe bağlı ipucu (ör. 'new costume launch')."""
    import anthropic  # yalnızca gerçekten üretilecekse gerekli

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY ortam değişkeninden okunur
    content = [_encode(p) for p in image_paths[:5]]
    content.append({"type": "text", "text": PROMPT.format(
        brief=os.getenv("CAPTION_BRIEF") or DEFAULT_BRIEF,
        n_tags=os.getenv("HASHTAG_COUNT", "8"),
        extra=f"Note about this post: {note}" if note else "No extra note.",
        fixed=os.getenv("FIXED_HASHTAGS", "ziboapp"),
    )})
    resp = client.messages.create(
        model=os.getenv("CAPTION_MODEL", "claude-sonnet-5"),
        max_tokens=600,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse(text)

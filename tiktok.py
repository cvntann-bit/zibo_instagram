"""TikTok Content Posting API (Direct Post) yayıncısı.

ÖNEMLİ: Denetimden (audit) geçmemiş uygulamalar yalnızca SELF_ONLY (sadece ben)
gizlilikle ve gizli hesaplara paylaşabilir. TikTok, "sadece kendi hesaplarını
yöneten araç"ları audit'te kabul etmediğini belirtiyor — kişisel bot için
paylaşımlar gizli düşer, sonra uygulamadan elle "Herkes"e açman gerekir.

- Videolar: FILE_UPLOAD ile yerel dosyadan parça parça yüklenir.
- Fotoğraflar: yalnızca PULL_FROM_URL (doğrulanmış domain) desteklenir.
Access token 24 saatte biter; refresh_token ile otomatik yenilenir (tokens.json).
"""
import json
import os
import time

import requests

API = "https://open.tiktokapis.com/v2"
MB = 1024 * 1024


class TikTokError(Exception):
    pass


class TikTokClient:
    def __init__(self, client_key: str, client_secret: str, token_file: str = "tokens.json",
                 privacy_level: str = "SELF_ONLY"):
        self.client_key = client_key
        self.client_secret = client_secret
        self.token_file = token_file
        self.privacy_level = privacy_level
        self.tokens = self._load_tokens()

    # ---------- token yönetimi ----------
    def _load_tokens(self) -> dict:
        if not os.path.exists(self.token_file):
            raise TikTokError(
                f"{self.token_file} yok. OAuth ile alınan access_token ve refresh_token'ı "
                "buraya yaz: {\"tiktok\": {\"access_token\": ..., \"refresh_token\": ...}}")
        with open(self.token_file, encoding="utf-8") as f:
            return json.load(f)

    def _save_tokens(self) -> None:
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(self.tokens, f, indent=2)

    def _refresh(self) -> None:
        resp = requests.post(f"{API}/oauth/token/", data={
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.tokens["tiktok"]["refresh_token"],
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        data = resp.json()
        if "access_token" not in data:
            raise TikTokError(f"Token yenilenemedi: {data}")
        self.tokens["tiktok"].update({
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", self.tokens["tiktok"]["refresh_token"]),
            "expires_at": int(time.time()) + int(data.get("expires_in", 86400)),
        })
        self._save_tokens()

    def _headers(self) -> dict:
        t = self.tokens["tiktok"]
        if t.get("expires_at", 0) - 300 < time.time():
            self._refresh()
        return {
            "Authorization": f"Bearer {self.tokens['tiktok']['access_token']}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _post(self, path: str, body: dict) -> dict:
        resp = requests.post(f"{API}{path}", headers=self._headers(), json=body, timeout=60)
        data = resp.json()
        err = data.get("error", {})
        if err.get("code") not in (None, "ok"):
            raise TikTokError(f"{err.get('code')}: {err.get('message')}")
        return data.get("data", {})

    # ---------- yardımcılar ----------
    def _privacy(self) -> str:
        info = self._post("/post/publish/creator_info/query/", {})
        options = info.get("privacy_level_options", [])
        if self.privacy_level in options:
            return self.privacy_level
        if "SELF_ONLY" in options:
            return "SELF_ONLY"
        raise TikTokError(f"Uygun gizlilik seçeneği yok: {options}")

    def _wait(self, publish_id: str, timeout: int = 600) -> str:
        start = time.time()
        while time.time() - start < timeout:
            data = self._post("/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status")
            if status == "PUBLISH_COMPLETE":
                return publish_id
            if status == "FAILED":
                raise TikTokError(f"Paylaşım başarısız: {data.get('fail_reason')}")
            time.sleep(10)
        raise TikTokError("TikTok işleme zaman aşımı")

    # ---------- genel API ----------
    def publish_video(self, video_path: str, caption: str) -> str:
        size = os.path.getsize(video_path)
        # 64 MB'a kadar tek parça; üstü 10 MB parçalar (son parça kalanı alır)
        if size <= 64 * MB:
            chunk_size, chunks = size, 1
        else:
            chunk_size = 10 * MB
            chunks = size // chunk_size

        data = self._post("/post/publish/video/init/", {
            "post_info": {
                "title": caption[:2200],
                "privacy_level": self._privacy(),
                "disable_comment": False,
                "disable_duet": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunks,
            },
        })
        publish_id, upload_url = data["publish_id"], data["upload_url"]

        with open(video_path, "rb") as f:
            for i in range(chunks):
                start = i * chunk_size
                end = size - 1 if i == chunks - 1 else start + chunk_size - 1
                f.seek(start)
                part = f.read(end - start + 1)
                resp = requests.put(upload_url, data=part, headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(part)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                }, timeout=600)
                if resp.status_code not in (200, 201, 206):
                    raise TikTokError(f"Parça {i + 1}/{chunks} yüklenemedi: {resp.status_code}")

        return self._wait(publish_id)

    def publish_photos(self, image_urls: list, caption: str, title: str = "") -> str:
        data = self._post("/post/publish/content/init/", {
            "media_type": "PHOTO",
            "post_mode": "DIRECT_POST",
            "post_info": {
                "title": title[:90],
                "description": caption[:4000],
                "privacy_level": self._privacy(),
            },
            "source_info": {
                "source": "PULL_FROM_URL",
                "photo_cover_index": 0,
                "photo_images": image_urls,
            },
        })
        return self._wait(data["publish_id"])

"""Instagram Graph API yayıncısı.

- Videolar (Reels): yerel dosyadan "resumable upload" ile yüklenir, public URL gerekmez.
- Görseller: Instagram yerel görsel yüklemeyi desteklemez, public bir URL ister
  (PUBLIC_BASE_URL ayarlıysa posts/ içindeki dosya bu adresten sunulmuş kabul edilir).
Gerekenler: Instagram Business/Creator hesap + uzun ömürlü token. İki yol da çalışır:
  - Instagram girişi (önerilen): instagram_business_basic, instagram_business_content_publish
  - Facebook girişi: instagram_basic, instagram_content_publish (+ bağlı Facebook Sayfası)
"""
import os
import time

import requests


class InstagramError(Exception):
    pass


class InstagramClient:
    def __init__(self, ig_user_id: str, access_token: str, api_version: str = "v23.0"):
        self.ig_user_id = ig_user_id
        self.token = access_token
        self.ver = api_version
        # "IG..." ile başlayan token = Instagram girişi (graph.instagram.com, Facebook Sayfası gerekmez)
        # "EAA..." ile başlayan token = Facebook girişi (graph.facebook.com)
        host = os.getenv("IG_API_HOST") or (
            "graph.instagram.com" if access_token.startswith("IG") else "graph.facebook.com")
        self.graph = f"https://{host}/{api_version}"
        if not str(self.ig_user_id).strip().isdigit():  # boş ya da "zibo.app" yazılmışsa tokenden bul
            self.ig_user_id = self.resolve_user_id()

    def resolve_user_id(self) -> str:
        if "graph.instagram.com" not in self.graph:
            raise InstagramError("Facebook girişi tokeninde Instagram kullanıcı ID'si sayı olarak girilmeli")
        data = self._check(requests.get(
            f"{self.graph}/me", params={"fields": "user_id,username", "access_token": self.token}, timeout=30))
        return str(data.get("user_id") or data["id"])

    # ---------- yardımcılar ----------
    def _check(self, resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise InstagramError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400 or "error" in data:
            raise InstagramError(str(data.get("error", data)))
        return data

    def _wait_until_ready(self, container_id: str, timeout: int = 600) -> None:
        start = time.time()
        while time.time() - start < timeout:
            data = self._check(requests.get(
                f"{self.graph}/{container_id}",
                params={"fields": "status_code,status", "access_token": self.token},
                timeout=30,
            ))
            status = data.get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Container {status}: {data.get('status')}")
            time.sleep(10)
        raise InstagramError("Video işleme zaman aşımına uğradı")

    def _publish(self, container_id: str) -> str:
        data = self._check(requests.post(
            f"{self.graph}/{self.ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": self.token},
            timeout=60,
        ))
        return data["id"]

    # ---------- genel API ----------
    def whoami(self) -> str:
        """Bağlantı testi: hesabın kullanıcı adını döndürür (ID self.ig_user_id'de)."""
        data = self._check(requests.get(
            f"{self.graph}/{self.ig_user_id}",
            params={"fields": "username", "access_token": self.token}, timeout=30))
        return data.get("username", "?")

    def publish_reel(self, video_path: str, caption: str, share_to_feed: bool = True) -> str:
        # 1) Container oluştur (resumable)
        data = self._check(requests.post(
            f"{self.graph}/{self.ig_user_id}/media",
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption,
                "share_to_feed": str(share_to_feed).lower(),
                "access_token": self.token,
            },
            timeout=60,
        ))
        container_id = data["id"]

        # 2) Dosyayı Meta sunucularına yükle
        size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            self._check(requests.post(
                f"https://rupload.facebook.com/ig-api-upload/{self.ver}/{container_id}",
                headers={
                    "Authorization": f"OAuth {self.token}",
                    "offset": "0",
                    "file_size": str(size),
                },
                data=f,
                timeout=600,
            ))

        # 3) İşlenmesini bekle, 4) yayınla
        self._wait_until_ready(container_id)
        return self._publish(container_id)

    def publish_reel_url(self, video_url: str, caption: str, share_to_feed: bool = True) -> str:
        """Reels: Instagram videoyu verilen linkten indirir."""
        data = self._check(requests.post(
            f"{self.graph}/{self.ig_user_id}/media",
            data={"media_type": "REELS", "video_url": video_url, "caption": caption,
                  "share_to_feed": str(share_to_feed).lower(), "access_token": self.token},
            timeout=60,
        ))
        self._wait_until_ready(data["id"])
        return self._publish(data["id"])

    def publish_image(self, image_url: str, caption: str) -> str:
        data = self._check(requests.post(
            f"{self.graph}/{self.ig_user_id}/media",
            data={"image_url": image_url, "caption": caption, "access_token": self.token},
            timeout=60,
        ))
        container_id = data["id"]
        self._wait_until_ready(container_id, timeout=120)
        return self._publish(container_id)

    def publish_carousel(self, image_urls: list, caption: str) -> str:
        """2-10 fotoğraflık kaydırmalı gönderi."""
        if not 2 <= len(image_urls) <= 10:
            raise InstagramError("Carousel 2 ile 10 arası fotoğraf ister")
        children = []
        for url in image_urls:
            data = self._check(requests.post(
                f"{self.graph}/{self.ig_user_id}/media",
                data={"image_url": url, "is_carousel_item": "true", "access_token": self.token},
                timeout=60,
            ))
            children.append(data["id"])
        for cid in children:
            self._wait_until_ready(cid, timeout=120)
        data = self._check(requests.post(
            f"{self.graph}/{self.ig_user_id}/media",
            data={"media_type": "CAROUSEL", "children": ",".join(children),
                  "caption": caption, "access_token": self.token},
            timeout=60,
        ))
        self._wait_until_ready(data["id"], timeout=120)
        return self._publish(data["id"])

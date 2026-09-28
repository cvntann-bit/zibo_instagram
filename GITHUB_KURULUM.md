# Zibo Bot — GitHub Actions kurulumu (bilgisayar kapalıyken paylaşım)

GitHub her gün **20:07** ve **02:07**'de (İstanbul) botu kendi sunucusunda çalıştırır,
kuyruktaki sıradaki postu Instagram'a atar ve `state.json`'u depoya geri kaydeder.

## 1. Depoyu oluştur (bir kez)
1. github.com → **New repository** → ad: `zibo-instagram` → **Private** → Create.
2. GitHub Desktop → File → **Add local repository** → bu klasörü seç → "create a repository" de.
3. Postları bu klasöre koy:
   - `ZiboBot_Uygulama\posts\` içindeki her şeyi → bu klasördeki `posts\` içine
   - `ZiboBot_Uygulama\posts.json` ve varsa `state.json` → bu klasörün köküne
   - İstersen `ZiboBot.exe`'yi de buraya taşı: aynı postları görür, düzenleme ve 🔍 Tara için kullanırsın.
4. GitHub Desktop → **Commit** → **Publish repository** (Private kalsın).

`.env` dosyan `.gitignore` sayesinde depoya **gitmez**. Anahtarlar yalnızca 2. adımdaki Secrets'ta durur.

## 2. Anahtarları ekle (Secrets)
Depo sayfası → **Settings → Secrets and variables → Actions → New repository secret**. Dört tane:

| Ad | Değer |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API anahtarın |
| `IMGBB_API_KEY` | imgbb anahtarın |
| `IG_ACCESS_TOKEN` | `IG...` ile başlayan Instagram tokenın |
| `IG_USER_ID` | ZiboBot'un test sonrası doldurduğu sayısal ID |

## 3. Dene
Depo → **Actions** → "Zibo Instagram paylaşımı" → **Run workflow**. 1–2 dk sonra post Instagram'da olmalı.
Telefondaki GitHub uygulamasından da aynı butonla istediğin an paylaşabilirsin.

## Günlük kullanım
- **Yeni post eklemek:** önce GitHub Desktop'ta **Pull** (bot `state.json`'u güncelliyor) → fotoğrafları `posts\`'a at →
  ZiboBot'ta 🔍 Tara (açıklamaları gözden geçir) → **Commit + Push**. Tara'yı atlarsan açıklamayı bot paylaşırken kendisi üretir.
- **ZiboBot'ta ▶ Botu başlat'a BASMA.** Hem bilgisayar hem GitHub paylaşırsa aynı post iki kez gidebilir. Bilgisayarda sadece Tara/düzenleme yap.
- **Saatleri değiştirmek:** `.github/workflows/instagram.yml` içindeki `cron` satırları (UTC: İstanbul saatinden 3 çıkar).
- **Hata olursa** GitHub sana e-posta atar; Actions sekmesinde kırmızı çalıştırmaya tıklayıp logu görürsün.
- **Instagram tokenı 60 günde biter:** Meta panelinden yeni token al → Secrets'taki `IG_ACCESS_TOKEN`'ı güncelle.

## Maliyet
Özel depoda ayda 2.000 dakika ücretsiz; her çalıştırma ~1 dakika → ayda ~60 dakika. Ücretsiz sınırın çok altında.

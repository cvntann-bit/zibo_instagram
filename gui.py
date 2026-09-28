"""Zibo Bot — basit masaüstü arayüzü.

Çift tıkla aç: posts/ klasörüne fotoğraf at → "Tara" → açıklamaları gözden geçir →
"Botu Başlat". Bot açık kaldığı sürece belirlenen saatlerde paylaşır.
Tüm dosyalar (.env, posts/, posts.json, state.json) programın yanındaki klasörde durur.
"""
import os
import queue
import subprocess
import sys
import threading
import importlib
import logging

# ---- programın klasörüne geç (exe ya da .py fark etmez) ----
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
os.chdir(APP_DIR)
if sys.stdout is None:  # pencereli exe'de konsol yok
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.makedirs("posts", exist_ok=True)
if not os.path.exists("posts.json"):
    with open("posts.json", "w", encoding="utf-8") as f:
        f.write("[]\n")

import tkinter as tk
from tkinter import messagebox, ttk

import main as bot

VERSION = "1.4"

SETTINGS = [
    ("ANTHROPIC_API_KEY", "Claude API anahtarı", True),
    ("IMGBB_API_KEY", "imgbb API anahtarı", True),
    ("IG_USER_ID", "Instagram kullanıcı ID (boş bırak, otomatik bulunur)", False),
    ("IG_ACCESS_TOKEN", "Instagram erişim tokenı", True),
    ("SLOTS", "Paylaşım saatleri (ör. 12:00,19:00)", False),
    ("FIXED_HASHTAGS", "Sabit hashtag'ler (virgülle)", False),
    ("AUTO_APPROVE", "Otomatik onay (1 = evet, 0 = ben onaylarım)", False),
]
DEFAULTS = {"SLOTS": "12:00,19:00", "FIXED_HASHTAGS": "ziboapp", "AUTO_APPROVE": "1"}
LOCK = threading.Lock()  # posts.json / state.json'a aynı anda iki yerden yazılmasın


# ---------------- .env ----------------
def read_env() -> dict:
    values = dict(DEFAULTS)
    if os.path.exists(".env"):
        with open(".env", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    values[k.strip()] = v.strip()
    return values


def write_env(updates: dict) -> None:
    lines, seen = [], set()
    if os.path.exists(".env"):
        with open(".env", encoding="utf-8") as f:
            for line in f:
                key = line.split("=", 1)[0].strip()
                if "=" in line and key in updates:
                    lines.append(f"{key}={updates[key]}\n")
                    seen.add(key)
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    lines += [f"{k}={v}\n" for k, v in updates.items() if k not in seen]
    with open(".env", "w", encoding="utf-8") as f:
        f.writelines(lines)


def reload_bot() -> None:
    global bot
    for k, v in read_env().items():
        os.environ[k] = v
    bot = importlib.reload(bot)


# ---------------- log → pencere ----------------
class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.q.put(self.format(record))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Zibo Bot v{VERSION}")
        self.geometry("900x620")
        self.minsize(700, 480)
        self.logq = queue.Queue()
        logging.getLogger().addHandler(QueueHandler(self.logq))
        self.stop_event = None
        self.worker = None

        # üst butonlar
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="📁 Fotoğraf klasörü", command=self.open_folder).pack(side="left")
        ttk.Button(top, text="🔍 Tara + açıklama üret", command=self.scan).pack(side="left", padx=6)
        ttk.Button(top, text="📤 Şimdi paylaş", command=self.post_now).pack(side="left")
        ttk.Button(top, text="⚙ Ayarlar", command=self.settings).pack(side="right")
        self.run_btn = ttk.Button(top, text="▶ Botu başlat", command=self.toggle_bot)
        self.run_btn.pack(side="right", padx=6)

        self.status = tk.StringVar(value="Bot durdu")
        ttk.Label(self, textvariable=self.status, padding=(8, 0)).pack(anchor="w")

        # gönderi listesi
        cols = ("foto", "durum", "zaman", "aciklama")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", height=12)
        self.tree.heading("#0", text="Gönderi")
        for c, t, w in [("foto", "Foto", 50), ("durum", "Durum", 130), ("zaman", "Zaman", 130),
                        ("aciklama", "Açıklama (düzenlemek için çift tıkla)", 380)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.column("#0", width=170)
        self.tree.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree.bind("<Double-1>", self.edit_selected)

        # log
        self.log = tk.Text(self, height=9, state="disabled", wrap="word")
        self.log.pack(fill="x", padx=8, pady=(0, 8))

        self.after(300, self.poll_log)
        self.refresh()
        if not read_env().get("ANTHROPIC_API_KEY"):
            self.after(500, lambda: messagebox.showinfo(
                "Hoş geldin", "Başlamadan önce ⚙ Ayarlar'dan anahtarlarını gir.\n"
                              "Sonra fotoğrafları 📁 klasöre at ve 🔍 Tara'ya bas."))

    # ---------- yardımcılar ----------
    def poll_log(self):
        got = False
        while not self.logq.empty():
            got = True
            self.log.configure(state="normal")
            self.log.insert("end", self.logq.get() + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        if got:
            self.refresh()
        self.after(300, self.poll_log)

    def background(self, fn):
        def run():
            try:
                with LOCK:
                    fn()
            except Exception as e:
                logging.getLogger("bot").error("Hata: %s", e)
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        state = bot.load_state()
        for item in bot.load_posts():
            done = state["posts"].get(item["id"], {})
            platforms = item.get("platforms", bot.DEFAULT_PLATFORMS)
            if all(done.get(p, {}).get("status") == "done" for p in platforms):
                durum = "✅ paylaşıldı"
            elif any(done.get(p, {}).get("status") == "failed" for p in platforms):
                durum = "⚠ hata (tekrar denenecek)"
            elif item.get("caption_error"):
                durum = "⚠ açıklama üretilemedi"
            elif not item.get("caption"):
                durum = "⏳ açıklama bekliyor"
            elif not item.get("approved", True):
                durum = "✋ onay bekliyor"
            else:
                durum = "🕒 sırada"
            zaman = (item.get("publish_at") or "sıradaki slot").replace("T", " ")
            cap = item.get("caption", "").replace("\n", " ")
            self.tree.insert("", "end", iid=item["id"], text=item["id"],
                             values=(len(bot.item_files(item)), durum, zaman, cap))

    # ---------- butonlar ----------
    def open_folder(self):
        path = os.path.join(APP_DIR, "posts")
        if sys.platform.startswith("win"):
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def scan(self):
        self.background(lambda: bot.scan(False))

    def post_now(self, pid=None):
        """Listeden seçili gönderiyi (seçim yoksa kuyruktaki sıradakini) hemen paylaşır."""
        if pid is None:
            sel = self.tree.selection()
            pid = sel[0] if sel else None
        what = f'"{pid}"' if pid else "Kuyruktaki sıradaki gönderi"
        if not messagebox.askyesno("Şimdi paylaş", f"{what} şimdi Instagram'a paylaşılacak. Emin misin?"):
            return

        def job():
            bot.scan(False)  # açıklaması yoksa önce üret
            state = bot.load_state()
            posts = bot.load_posts()
            if pid:
                item = next((p for p in posts if p["id"] == pid), None)
            else:
                item = bot.next_queue_item(posts, state)
            if not item:
                bot.log.info("Paylaşılacak gönderi bulunamadı")
            elif not item.get("caption"):
                bot.log.error("%s: açıklama yok (Claude anahtarını kontrol et ya da elle yaz)", item["id"])
            elif not bot.pending_platforms(item, state):
                bot.log.info("%s zaten paylaşılmış", item["id"])
            else:
                bot.log.info("Şimdi paylaşılıyor: %s", item["id"])
                bot.post_item(item, state, False)
        self.background(job)

    def toggle_bot(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.run_btn.config(text="▶ Botu başlat")
            self.status.set("Bot durdu")
            bot.log.info("Bot durduruldu")
            return
        self.stop_event = threading.Event()
        stop = self.stop_event

        def loop():
            state = bot.load_state()
            while not stop.is_set():
                try:
                    with LOCK:
                        bot.tick(state, False)
                except Exception as e:
                    bot.log.error("Döngü hatası: %s", e)
                stop.wait(bot.CHECK_EVERY_SEC)
        self.worker = threading.Thread(target=loop, daemon=True)
        self.worker.start()
        self.run_btn.config(text="⏸ Botu durdur")
        self.status.set(f"Bot çalışıyor — paylaşım saatleri: {', '.join(bot.SLOTS)}  (pencereyi kapatma)")
        bot.log.info("Bot başladı | slotlar: %s", ", ".join(bot.SLOTS))

    def settings(self):
        win = tk.Toplevel(self)
        win.title("Ayarlar")
        win.transient(self)
        values = read_env()
        entries = {}
        for i, (key, label, secret) in enumerate(SETTINGS):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            e = ttk.Entry(win, width=48, show="•" if secret else "")
            e.insert(0, values.get(key, ""))
            e.grid(row=i, column=1, padx=8, pady=4)
            entries[key] = e

        def save():
            try:
                slots = bot.parse_slots(entries["SLOTS"].get())
            except ValueError as e:
                messagebox.showerror("Paylaşım saatleri", str(e), parent=win)
                return
            entries["SLOTS"].delete(0, "end")
            entries["SLOTS"].insert(0, ",".join(slots))
            write_env({k: e.get().strip() for k, e in entries.items()})
            running = self.worker and self.worker.is_alive()
            if running:
                self.toggle_bot()
            reload_bot()
            if running:
                self.toggle_bot()
            bot.log.info("Ayarlar kaydedildi")
            win.destroy()
        def test_ig():
            try:
                from instagram import InstagramClient
                ig = InstagramClient(entries["IG_USER_ID"].get().strip(),
                                     entries["IG_ACCESS_TOKEN"].get().strip())
                name = ig.whoami()
                entries["IG_USER_ID"].delete(0, "end")
                entries["IG_USER_ID"].insert(0, ig.ig_user_id)
                messagebox.showinfo("Instagram", f"Bağlantı tamam: @{name}\nID otomatik dolduruldu. Kaydet'e bas.", parent=win)
            except Exception as e:
                messagebox.showerror("Instagram bağlanamadı", str(e)[:400], parent=win)

        ttk.Button(win, text="Instagram'ı test et", command=test_ig).grid(row=len(SETTINGS), column=0, sticky="w", padx=8, pady=8)
        ttk.Button(win, text="Kaydet", command=save).grid(row=len(SETTINGS), column=1, sticky="e", padx=8, pady=8)

    def edit_selected(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        pid = sel[0]
        posts = bot.load_posts()
        item = next((p for p in posts if p["id"] == pid), None)
        if not item:
            return
        win = tk.Toplevel(self)
        win.title(f"Düzenle — {pid}")
        win.transient(self)

        ttk.Label(win, text="Açıklama").pack(anchor="w", padx=8, pady=(8, 0))
        cap = tk.Text(win, width=60, height=6, wrap="word")
        cap.insert("1.0", item.get("caption", ""))
        cap.pack(padx=8)
        ttk.Label(win, text="Hashtag'ler (boşlukla ayır, # gerekmez)").pack(anchor="w", padx=8, pady=(8, 0))
        tags = ttk.Entry(win, width=60)
        tags.insert(0, " ".join(item.get("hashtags", [])))
        tags.pack(padx=8)
        ttk.Label(win, text="Paylaşım zamanı (boş = sıradaki slot, ör. 2026-10-03T20:30)").pack(anchor="w", padx=8, pady=(8, 0))
        when = ttk.Entry(win, width=30)
        when.insert(0, item.get("publish_at", ""))
        when.pack(anchor="w", padx=8)
        approved = tk.BooleanVar(value=item.get("approved", True))
        ttk.Checkbutton(win, text="Onaylı (paylaşılabilir)", variable=approved).pack(anchor="w", padx=8, pady=8)

        def save(regen=False, now=False):
            publish_at = when.get().strip()
            if publish_at:
                try:
                    bot.parse_time(publish_at)
                except ValueError:
                    messagebox.showerror("Hatalı tarih", "Biçim: 2026-10-03T20:30", parent=win)
                    return
            with LOCK:
                posts = bot.load_posts()
                for p in posts:
                    if p["id"] == pid:
                        p["caption"] = "" if regen else cap.get("1.0", "end").strip()
                        p["hashtags"] = [] if regen else [t.lstrip("#") for t in tags.get().split() if t]
                        p["approved"] = approved.get()
                        if publish_at:
                            p["publish_at"] = publish_at
                        else:
                            p.pop("publish_at", None)
                        p.pop("caption_error", None)
                bot._write_json(bot.POSTS_JSON, posts)
            win.destroy()
            self.refresh()
            if regen:
                self.scan()
            if now:
                self.post_now(pid)

        row = ttk.Frame(win)
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(row, text="🔄 Yeniden üret", command=lambda: save(True)).pack(side="left")
        ttk.Button(row, text="Kaydet", command=save).pack(side="right")
        ttk.Button(row, text="📤 Kaydet ve şimdi paylaş", command=lambda: save(now=True)).pack(side="right", padx=6)


if __name__ == "__main__":
    App().mainloop()

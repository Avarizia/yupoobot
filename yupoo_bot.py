#!/usr/bin/env python3
"""
Yupoo Downloader Bot v4
pip install python-telegram-bot requests beautifulsoup4
"""

import os, re, io, sys, time, logging, zipfile
from datetime import datetime
from urllib.parse import urlparse, urljoin

try:
    import requests
    from bs4 import BeautifulSoup
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
        "python-telegram-bot", "requests", "beautifulsoup4"])
    import requests
    from bs4 import BeautifulSoup
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("YUPOO_BOT_TOKEN", "METTI_QUI_IL_TUO_TOKEN")
TG_LIMIT  = 50 * 1024 * 1024


# ═══════════════════════════════════════════════
#  DOWNLOADER CORE
# ═══════════════════════════════════════════════
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8",
    "Referer": "https://www.yupoo.com/",
}
API_REST  = "https://www.yupoo.com/api/rest/"
PHOTO_CDN = "https://photo.yupoo.com"
VIDEO_CDN = "https://video.yupoo.com"

def get_session():
    s = requests.Session(); s.headers.update(HEADERS); return s

def fetch_url(session, url, retries=3, params=None, as_json=False):
    for attempt in range(retries):
        try:
            h = dict(session.headers)
            if as_json:
                h["Accept"] = "application/json, text/plain, */*"
                h["X-Requested-With"] = "XMLHttpRequest"
            r = session.get(url, headers=h, params=params, timeout=20)
            r.raise_for_status(); return r
        except requests.RequestException:
            if attempt < retries - 1: time.sleep(2)
            else: raise

def extract_api_key(html):
    for pat in [r"[,{]apiKey\s*:\s*['\"]([a-f0-9]+)['\"]", r"api_key['\"]?\s*[:=]\s*['\"]([a-f0-9]+)['\"]"]:
        m = re.search(pat, html, re.IGNORECASE)
        if m: return m.group(1)
    return ""

def yupoo_api(session, method, params):
    p = {"method": method, "format": "json", "nojsoncallback": "1", **params}
    return fetch_url(session, API_REST, params=p, as_json=True).json()

def build_photo_url(ph):
    b, k, s = ph.get("bucket",""), ph.get("key",""), ph.get("secret","")
    fmt = ph.get("originalformat") or ph.get("format") or "jpg"
    if b and k and s: return f"{PHOTO_CDN}/{b}/{k}/{s}.{fmt}"
    p = ph.get("path") or ph.get("src") or ph.get("url") or ""
    if p.startswith("http"): return p
    if p.startswith("//"): return "https:"+p
    return f"{PHOTO_CDN}/{p.lstrip('/')}" if p else ""

def build_video_url(ph):
    v = ph.get("video") or {}
    if isinstance(v, dict):
        for k in ("mp4","url","src"):
            u = v.get(k,"")
            if u: return u if u.startswith("http") else "https:"+u
    b, k, s = ph.get("bucket",""), ph.get("key",""), ph.get("secret","")
    if b and k and s: return f"{VIDEO_CDN}/{b}/{k}/{s}.mp4"
    return ""

def get_ext(url, mtype="image"):
    if mtype == "video":
        m = re.search(r"\.(mp4|mov|avi|webm)(\?|$)", url, re.I)
        return "."+m.group(1).lower() if m else ".mp4"
    m = re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.I)
    return "."+m.group(1).lower() if m else ".jpg"

def fetch_via_api(session, album_url):
    html = fetch_url(session, album_url).text
    api_key = extract_api_key(html)
    if not api_key: return []
    m = re.search(r"/albums/(\d+)", album_url)
    if not m: return []
    album_id = m.group(1)
    media, page = [], 1
    while True:
        data = yupoo_api(session, "yupoo.albums.getPhotos",
                         {"api_key": api_key, "album_id": album_id, "page": page, "per_page": 100})
        if data.get("stat") != "ok": break
        pobj = data.get("photos", {})
        plist = pobj.get("photo", [])
        npages = int(pobj.get("pages", 1))
        for ph in plist:
            pid = ph.get("id") or ph.get("photo_id") or ""
            try:
                info = yupoo_api(session, "yupoo.photos.getInfo", {"api_key": api_key, "photo_id": pid})
                if info.get("stat") == "ok":
                    pdata = info.get("photo", {})
                    if pdata.get("media") == "video":
                        vurl = build_video_url(pdata) or re.sub(r"\.\w+$", ".mp4", build_photo_url(pdata))
                        if vurl: media.append({"url": vurl, "type": "video"})
                    else:
                        u = build_photo_url(pdata)
                        if u: media.append({"url": u, "type": "image"})
                else:
                    u = build_photo_url(ph)
                    if u: media.append({"url": u, "type": "image"})
            except Exception:
                u = build_photo_url(ph)
                if u: media.append({"url": u, "type": "image"})
            time.sleep(0.04)
        if page >= npages: break
        page += 1; time.sleep(0.3)
    return media

def fetch_photo_video_url(session, base_url, photo_id):
    try:
        soup = BeautifulSoup(fetch_url(session, f"{base_url}/photos/{photo_id}").text, "html.parser")
        for el in soup.find_all(["video","source"]):
            u = el.get("src") or el.get("data-src") or ""
            if u and "undefined" not in u:
                if not u.startswith("http"): u = "https:"+u if u.startswith("//") else base_url+u
                return u
        for script in soup.find_all("script"):
            for pat in [r'"videoUrl"\s*:\s*"([^"]+)"', r'"mp4"\s*:\s*"([^"]+)"']:
                m = re.search(pat, script.string or "")
                if m:
                    u = m.group(1).replace("\\/","/")
                    if "undefined" not in u: return u
    except Exception: pass
    return ""

def fetch_via_html(session, album_url):
    parsed = urlparse(album_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    resp = fetch_url(session, album_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    pages = [album_url]; seen_p = {album_url}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"[?&]page=\d+", href):
            full = urljoin(base_url, href) if not href.startswith("http") else href
            clean = re.sub(r"[&?]_=\d+", "", full)
            if clean not in seen_p: seen_p.add(clean); pages.append(full)
    media = []
    for i, purl in enumerate(pages, 1):
        if i > 1: soup = BeautifulSoup(fetch_url(session, purl).text, "html.parser")
        for img in soup.find_all("img", attrs={"data-origin-src": True}):
            u = img["data-origin-src"].strip()
            if not u: continue
            if not u.startswith("http"): u = urljoin(base_url, u)
            media.append({"url": u.split("?")[0], "type": "image"})
        for container in soup.find_all(attrs={"data-type": "video"}):
            pid = container.get("data-id") or container.get("data-photo-id") or ""
            if pid:
                vurl = fetch_photo_video_url(session, base_url, pid)
                if vurl: media.append({"url": vurl, "type": "video"})
        for el in soup.find_all(["video","source"]):
            u = el.get("src") or el.get("data-src") or ""
            if not u or "undefined" in u: continue
            if not u.startswith("http"): u = "https:"+u if u.startswith("//") else urljoin(base_url, u)
            media.append({"url": u, "type": "video"})
        if i < len(pages): time.sleep(0.5)
    return media

def collect_media(album_url):
    session = get_session()
    try:
        media = fetch_via_api(session, album_url)
        if media: return media
    except Exception: pass
    try: return fetch_via_html(session, album_url)
    except Exception: return []

def download_bytes(session, url, mtype):
    try:
        r = session.get(url, timeout=180 if mtype=="video" else 30, stream=True)
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type",""): return None
        data = b""
        for chunk in r.iter_content(65536): data += chunk
        return data
    except Exception: return None

def fetch_category_covers(session, cat_url: str) -> list[dict]:
    """
    Scarica la lista album da una pagina categoria Yupoo
    e ritorna la copertina (prima foto) di ogni album.
    Ritorna lista di dict: {"url": str, "title": str, "album_id": str}
    """
    parsed   = urlparse(cat_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html     = fetch_url(session, cat_url).text
    api_key  = extract_api_key(html)
    m        = re.search(r"/categories/(\d+)", cat_url)
    cat_id   = m.group(1) if m else ""

    covers = []

    # Strategia 1: API REST
    if api_key and cat_id:
        page = 1
        while True:
            data = yupoo_api(session, "yupoo.albums.getList", {
                "api_key":  api_key,
                "cat_id":   cat_id,
                "page":     page,
                "per_page": 100,
            })
            if data.get("stat") != "ok":
                break
            aobj   = data.get("albums", {})
            alist  = aobj.get("album", [])
            npages = int(aobj.get("pages", 1))

            for album in alist:
                album_id = str(album.get("id") or album.get("album_id") or "")
                title    = album.get("title") or album.get("name") or album_id

                # Prendi la prima foto dell'album
                cover_url = ""
                # Campo cover diretto
                cover = album.get("cover") or album.get("coverPhoto") or {}
                if isinstance(cover, dict):
                    cover_url = build_photo_url(cover)
                # Oppure vai a prendere la prima foto
                if not cover_url and album_id and api_key:
                    try:
                        pd = yupoo_api(session, "yupoo.albums.getPhotos", {
                            "api_key": api_key, "album_id": album_id,
                            "page": 1, "per_page": 1,
                        })
                        if pd.get("stat") == "ok":
                            photos = pd.get("photos", {}).get("photo", [])
                            if photos:
                                cover_url = build_photo_url(photos[0])
                    except Exception:
                        pass

                if cover_url:
                    covers.append({"url": cover_url, "title": title, "album_id": album_id})
                time.sleep(0.05)

            if page >= npages:
                break
            page += 1
            time.sleep(0.3)

    # Strategia 2: HTML fallback — cerca thumbnail degli album nella pagina
    if not covers:
        soup = BeautifulSoup(html, "html.parser")
        for a_tag in soup.find_all("a", href=re.compile(r"/albums/\d+")):
            href = a_tag.get("href", "")
            m2   = re.search(r"/albums/(\d+)", href)
            if not m2:
                continue
            album_id = m2.group(1)
            # Cerca immagine di copertina dentro il tag
            img = a_tag.find("img")
            if not img:
                continue
            url = img.get("data-origin-src") or img.get("data-src") or img.get("src") or ""
            if not url or "undefined" in url:
                continue
            if not url.startswith("http"):
                url = urljoin(base_url, url)
            url   = url.split("?")[0]
            title = a_tag.get("title") or img.get("alt") or album_id
            # Evita duplicati
            if not any(c["album_id"] == album_id for c in covers):
                covers.append({"url": url, "title": title, "album_id": album_id})

    return covers


def make_filename(idx: int, url: str, mtype: str, seller: str, album_id: str) -> str:
    """Genera nome file con seller + album_id invece del semplice numero."""
    ext = get_ext(url, mtype)
    return f"{seller}_{album_id}_{idx:03d}{ext}"


# ═══════════════════════════════════════════════
#  STATO UTENTI
# ═══════════════════════════════════════════════
sessions: dict[int, dict] = {}
stop_events: dict[int, bool] = {}
# Coda album: uid -> lista di url in attesa
queues: dict[int, list] = {}

def get_user(uid: int) -> dict:
    if uid not in sessions:
        sessions[uid] = {
            "url": None,
            "history": [],
            "total_albums": 0,
            "total_files": 0,
            "total_mb": 0.0,
            "started": datetime.now(),
        }
    return sessions[uid]


# ═══════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════
def kb_preview():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 Album Telegram", callback_data="mode_album"),
            InlineKeyboardButton("🗜 ZIP",            callback_data="mode_zip"),
        ],
        [
            InlineKeyboardButton("📷 Solo foto",  callback_data="mode_photos"),
            InlineKeyboardButton("🎬 Solo video", callback_data="mode_videos"),
        ],
        [InlineKeyboardButton("❌ Annulla", callback_data="cancel")],
    ])

def kb_stop():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏹ Stop download", callback_data="stop_download"),
    ]])

def kb_after_download(has_queue: bool = False):
    rows = []
    if has_queue:
        rows.append([InlineKeyboardButton("▶️ Prossimo in coda", callback_data="next_queue")])
    rows.append([InlineKeyboardButton("⬇️ Scarica un altro album", callback_data="new_download")])
    rows.append([
        InlineKeyboardButton("📊 Statistiche", callback_data="show_stats"),
        InlineKeyboardButton("📋 Cronologia",  callback_data="show_history"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_back():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Torna al menu", callback_data="new_download"),
    ]])


# ═══════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════
def fmt_mb(b: float) -> str:
    if b < 1: return f"{b*1024:.0f} KB"
    return f"{b:.1f} MB"

def progress_bar(current: int, total: int, width: int = 12) -> str:
    filled = int(width * current / total) if total else 0
    return "█" * filled + "░" * (width - filled)

def parse_album_info(url: str) -> tuple[str, str]:
    """Ritorna (seller, album_id) dall'URL."""
    seller   = urlparse(url).netloc.split(".")[0]
    m        = re.search(r"/albums/(\d+)", url)
    album_id = m.group(1) if m else "0"
    return seller, album_id


# ═══════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.first_name or "ciao"
    get_user(uid)
    await update.message.reply_text(
        f"👋 *Ciao {name}!*\n\n"
        "Sono il tuo downloader per album Yupoo.\n"
        "Manda un link (o più link in fila) e scarico tutto.\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📌 *Comandi*\n"
        "/start   — questo messaggio\n"
        "/help    — come funziono\n"
        "/stats   — statistiche\n"
        "/history — ultimi album\n"
        "/annulla — ferma il download\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "👇 *Manda un link album Yupoo*",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Come funziono*\n\n"
        "1️⃣ Manda uno o più link Yupoo (anche più messaggi di fila)\n"
        "2️⃣ Vedi l'anteprima con numero foto/video\n"
        "3️⃣ Scegli la modalità:\n"
        "   🖼 *Album Telegram* — foto in anteprima a gruppi\n"
        "   🗜 *ZIP* — file compresso unico\n"
        "   📷 *Solo foto* — ignora i video\n"
        "   🎬 *Solo video* — ignora le foto\n\n"
        "📁 I file si chiamano `seller_albumid_001.jpg`\n"
        "⏹ Puoi stoppare in qualsiasi momento\n"
        "🔢 Più link = coda automatica\n\n"
        "⚠️ Limite Telegram: 50 MB per file",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    data = get_user(uid)
    mins = int((datetime.now() - data["started"]).total_seconds() / 60)
    q    = queues.get(uid, [])
    txt  = (
        "📊 *Le tue statistiche*\n\n"
        f"📦 Album scaricati:  `{data['total_albums']}`\n"
        f"🖼 File ricevuti:    `{data['total_files']}`\n"
        f"💾 Dati scaricati:  `{fmt_mb(data['total_mb'])}`\n"
        f"⏱ Sessione attiva: `{mins} min`"
    )
    if q:
        txt += f"\n📋 In coda: `{len(q)}` album"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back())

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    history = get_user(uid).get("history", [])
    if not history:
        await update.message.reply_text("📋 Nessun album scaricato ancora.", reply_markup=kb_back())
        return
    lines = "\n".join(f"`{i+1}.` {url}" for i, url in enumerate(history[-10:]))
    await update.message.reply_text(
        f"📋 *Ultimi album*\n\n{lines}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back(),
    )

async def cmd_annulla(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    stop_events[uid] = True
    queues[uid] = []
    await update.message.reply_text("⏹ Download interrotto e coda svuotata.")


# ═══════════════════════════════════════════════
#  HANDLER MESSAGGI (link)
# ═══════════════════════════════════════════════
async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    uid  = update.effective_user.id

    is_album    = "yupoo.com" in text and "/albums/" in text
    is_category = "yupoo.com" in text and "/categories/" in text

    if not is_album and not is_category:
        await update.message.reply_text(
            "❌ Link non valido.\n\n"
            "Album: `https://seller.x.yupoo.com/albums/123456789`\n"
            "Categoria: `https://seller.x.yupoo.com/categories/123456789`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Categoria → scarica copertine
    if is_category:
        if get_user(uid).get("downloading"):
            await update.message.reply_text("⏳ Download in corso, aspetta che finisca.")
            return
        await _handle_category(update.message, text, uid)
        return

    seller, album_id = parse_album_info(text)

    # Se c'è già un download in corso, aggiungi alla coda
    if get_user(uid).get("downloading"):
        q = queues.setdefault(uid, [])
        if text not in q:
            q.append(text)
        await update.message.reply_text(
            f"📋 Aggiunto in coda — posizione `{len(q)}`\n"
            f"🏪 `{seller}` — album `#{album_id}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    get_user(uid)["url"] = text
    await _show_preview(update.message, text, seller, album_id)


async def _handle_category(message, cat_url: str, uid: int):
    """Gestisce un link categoria: scarica la copertina di ogni album."""
    parsed   = urlparse(cat_url)
    seller   = parsed.netloc.split(".")[0]
    m        = re.search(r"/categories/(\d+)", cat_url)
    cat_id   = m.group(1) if m else "?"

    msg = await message.reply_text(
        f"🔍 Analizzo categoria `#{cat_id}` di `{seller}`...",
        parse_mode=ParseMode.MARKDOWN,
    )

    session = get_session()
    try:
        covers = fetch_category_covers(session, cat_url)
    except Exception as e:
        await msg.edit_text(f"❌ Errore: {e}")
        return

    if not covers:
        await msg.edit_text(
            "❌ Nessun album trovato in questa categoria.\n"
            "La categoria potrebbe essere privata o vuota."
        )
        return

    await msg.edit_text(
        f"📂 *Categoria `#{cat_id}`* — `{seller}`\n\n"
        f"📦 Album trovati: `{len(covers)}`\n"
        f"🖼 Scarico una copertina per album...\n\n"
        f"Come vuoi riceverle?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🖼 Album Telegram", callback_data=f"cat_album"),
                InlineKeyboardButton("🗜 ZIP",            callback_data=f"cat_zip"),
            ],
            [InlineKeyboardButton("❌ Annulla", callback_data="cancel")],
        ]),
    )
    # Salva i dati per il callback
    get_user(uid)["cat_covers"] = covers
    get_user(uid)["cat_seller"] = seller
    get_user(uid)["cat_id"]     = cat_id


async def _show_preview(message, url: str, seller: str, album_id: str):
    """Analizza l'album e mostra anteprima con conteggio foto/video."""
    msg = await message.reply_text(
        f"🔍 Analizzo `{seller}` — album `#{album_id}`...",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        session = get_session()
        html    = fetch_url(session, url).text
        api_key = extract_api_key(html)
        n_img = n_vid = n_total = 0
        title = ""

        if api_key:
            m = re.search(r"/albums/(\d+)", url)
            if m:
                d = yupoo_api(session, "yupoo.albums.getPhotos",
                              {"api_key": api_key, "album_id": m.group(1), "page": 1, "per_page": 1})
                if d.get("stat") == "ok":
                    n_total = int(d.get("photos", {}).get("total", 0))
                    soup    = BeautifulSoup(html, "html.parser")
                    n_vid   = len(soup.find_all(attrs={"data-type": "video"}))
                    n_img   = max(0, n_total - n_vid)
                    t = soup.find("title")
                    if t: title = t.text.split("|")[0].strip()[:55]

        if n_total == 0:
            soup    = BeautifulSoup(html, "html.parser")
            n_img   = len(soup.find_all("img", attrs={"data-origin-src": True}))
            n_vid   = len(soup.find_all(attrs={"data-type": "video"}))
            n_total = n_img + n_vid
            t = soup.find("title")
            if t: title = t.text.split("|")[0].strip()[:55]

        preview = f"📋 *Anteprima album*\n\n🏪 `{seller}` — `#{album_id}`\n"
        if title: preview += f"📝 _{title}_\n"
        preview += (
            f"\n🖼 Foto:   `{n_img}`\n"
            f"🎬 Video:  `{n_vid}`\n"
            f"📦 Totale: `{n_total}`\n\n"
            f"Come vuoi scaricare?"
        )
        await msg.edit_text(preview, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_preview())

    except Exception:
        await msg.edit_text(
            f"🔗 *Album pronto*\n🏪 `{seller}` — `#{album_id}`\n\nCome vuoi scaricare?",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_preview(),
        )


# ═══════════════════════════════════════════════
#  CALLBACK HANDLER
# ═══════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    if data in ("cat_album", "cat_zip"):
        s       = get_user(uid)
        covers  = s.get("cat_covers", [])
        seller  = s.get("cat_seller", "seller")
        cat_id  = s.get("cat_id", "0")
        mode_zip = (data == "cat_zip")

        if not covers:
            await query.edit_message_text("❌ Dati categoria scaduti, manda di nuovo il link.")
            return

        await query.edit_message_text(
            f"⬇️ Scarico `{len(covers)}` copertine...",
            parse_mode=ParseMode.MARKDOWN,
        )
        status = await query.message.reply_text(
            f"`{progress_bar(0, len(covers))}` 0%",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_stop(),
        )

        s["downloading"] = True
        stop_events[uid] = False
        session = get_session()
        ok = 0; total_bytes = 0; batch = []

        async def flush_batch(b):
            nonlocal ok, total_bytes
            tg_media = []
            for i, (data_b, fname, title) in enumerate(b):
                buf = io.BytesIO(data_b); buf.name = fname
                cap = f"`{fname}`\n_{title}_" if i == 0 else None
                tg_media.append(InputMediaPhoto(media=buf, caption=cap, parse_mode=ParseMode.MARKDOWN))
                ok += 1; total_bytes += len(data_b)
            try:
                await query.message.reply_media_group(media=tg_media)
            except Exception:
                for data_b, fname, _ in b:
                    buf = io.BytesIO(data_b); buf.name = fname
                    try: await query.message.reply_document(document=buf, filename=fname)
                    except Exception: pass

        zip_buf = io.BytesIO()
        zf      = zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_STORED) if mode_zip else None

        for idx, cover in enumerate(covers, 1):
            if stop_events.get(uid): break
            url   = cover["url"]
            title = cover["title"][:40]
            fname = f"{seller}_{cat_id}_cover{idx:03d}{get_ext(url)}"
            pct   = int(idx / len(covers) * 100)
            try:
                await status.edit_text(
                    f"⬇️ *{idx}/{len(covers)}* — `{fname}`\n\n"
                    f"`{progress_bar(idx, len(covers))}` {pct}%",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb_stop(),
                )
            except Exception: pass

            data_b = download_bytes(session, url, "image")
            if not data_b:
                continue

            if mode_zip and zf:
                zf.writestr(fname, data_b)
                ok += 1; total_bytes += len(data_b)
            else:
                batch.append((data_b, fname, title))
                if len(batch) == 10:
                    await flush_batch(batch); batch = []; time.sleep(0.3)

            time.sleep(0.15)

        if not mode_zip and batch:
            await flush_batch(batch)

        if mode_zip and zf:
            zf.close()
            zip_buf.seek(0)
            zip_name = f"{seller}_cat{cat_id}_covers.zip"
            zb = zip_buf.getvalue()
            try:
                await status.edit_text(f"📤 Invio ZIP `{zip_name}` ({len(zb)//1024} KB)...", parse_mode=ParseMode.MARKDOWN)
            except Exception: pass
            b2 = io.BytesIO(zb); b2.name = zip_name
            await query.message.reply_document(document=b2, filename=zip_name, caption=f"📂 {ok} copertine")

        s["downloading"] = False
        s["total_files"] += ok
        s["total_mb"]    += total_bytes / 1024 / 1024

        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        try: await status.delete()
        except Exception: pass

        await query.message.reply_text(
            f"✅ *Copertine scaricate!*\n"
            f"📅 `{now}`\n\n"
            f"📂 Categoria: `#{cat_id}`\n"
            f"🖼 Copertine: `{ok}/{len(covers)}`\n\n"
            f"*Cosa vuoi fare adesso?*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_after_download(),
            disable_notification=True,
        )
        return

    if data == "cancel":
        await query.edit_message_text("❌ Annullato.")
        return

    if data == "new_download":
        await query.edit_message_text(
            "👇 *Manda il link del prossimo album Yupoo*\n"
            "_Puoi mandare più link di fila per metterli in coda._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "show_stats":
        s    = get_user(uid)
        mins = int((datetime.now() - s["started"]).total_seconds() / 60)
        await query.edit_message_text(
            "📊 *Statistiche*\n\n"
            f"📦 Album: `{s['total_albums']}`\n"
            f"🖼 File:  `{s['total_files']}`\n"
            f"💾 Dati: `{fmt_mb(s['total_mb'])}`\n"
            f"⏱ Sessione: `{mins} min`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back(),
        )
        return

    if data == "show_history":
        history = get_user(uid).get("history", [])
        if not history:
            await query.edit_message_text("📋 Nessun album ancora.", reply_markup=kb_back())
            return
        lines = "\n".join(f"`{i+1}.` {u}" for i, u in enumerate(history[-10:]))
        await query.edit_message_text(
            f"📋 *Ultimi album*\n\n{lines}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back(),
        )
        return

    if data == "stop_download":
        stop_events[uid] = True
        try:
            await query.edit_message_text(
                "⏹ *Stop richiesto* — fermo dopo il file corrente...",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception: pass
        return

    if data == "next_queue":
        q = queues.get(uid, [])
        if not q:
            await query.edit_message_text("📋 Coda vuota.")
            return
        next_url = q.pop(0)
        get_user(uid)["url"] = next_url
        seller, album_id = parse_album_info(next_url)
        await query.edit_message_text(f"▶️ Prossimo album: `{seller}` `#{album_id}`...", parse_mode=ParseMode.MARKDOWN)
        await _show_preview(query.message, next_url, seller, album_id)
        return

    # ── Modalità download ──
    mode_map = {
        "mode_album":  ("all",    False),
        "mode_zip":    ("all",    True),
        "mode_photos": ("photos", False),
        "mode_videos": ("videos", False),
    }
    if data not in mode_map:
        return

    filter_type, mode_zip = mode_map[data]

    s = get_user(uid)
    album_url = s.get("url")
    if not album_url:
        await query.edit_message_text("❌ Sessione scaduta, manda di nuovo il link.")
        return

    seller, album_id = parse_album_info(album_url)
    mode_labels = {
        "mode_album":  "Album Telegram 🖼",
        "mode_zip":    "ZIP 🗜",
        "mode_photos": "Solo foto 📷",
        "mode_videos": "Solo video 🎬",
    }
    await query.edit_message_text(
        f"⏳ *{mode_labels[data]}*\n`{seller}` — `#{album_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    status = await query.message.reply_text("🔍 Raccolta media in corso...")
    s["downloading"] = True
    stop_events[uid] = False

    try:
        media = collect_media(album_url)
    except Exception as e:
        s["downloading"] = False
        await status.edit_text(f"❌ Errore: {e}")
        return

    if not media:
        s["downloading"] = False
        await status.edit_text(
            "❌ *Nessun media trovato*\n\n• Album privato?\n• Link non valido?",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_back(),
        )
        return

    # Applica filtro tipo
    if filter_type == "photos":
        media = [x for x in media if x["type"] == "image"]
    elif filter_type == "videos":
        media = [x for x in media if x["type"] == "video"]

    if not media:
        s["downloading"] = False
        await status.edit_text("❌ Nessun file del tipo selezionato trovato.")
        return

    n_img = sum(1 for x in media if x["type"] == "image")
    n_vid = sum(1 for x in media if x["type"] == "video")
    try:
        await status.edit_text(
            f"📦 *{n_img}* foto + *{n_vid}* video\n"
            f"⬇️ Download in corso...\n\n"
            f"`{progress_bar(0, len(media))}` 0%",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_stop(),
        )
    except Exception: pass

    session = get_session()
    ok = 0; total_bytes = 0

    if mode_zip:
        ok, total_bytes = await _send_zip(query.message, session, media, album_url, seller, album_id, status, uid)
    else:
        ok, total_bytes = await _send_album(query.message, session, media, seller, album_id, status, uid)

    s["downloading"] = False
    s["total_albums"] += 1
    s["total_files"]  += ok
    s["total_mb"]     += total_bytes / 1024 / 1024
    if album_url not in s["history"]:
        s["history"].append(album_url)

    was_stopped = stop_events.get(uid, False)
    now         = datetime.now().strftime("%d.%m.%Y %H:%M")
    skipped     = len(media) - ok
    q_remaining = queues.get(uid, [])

    summary = (
        f"{'⏹' if was_stopped else '✅'} *{'Interrotto' if was_stopped else 'Completato'}!*\n"
        f"📅 `{now}`\n\n"
        f"🏪 `{seller}` — `#{album_id}`\n"
        f"🖼 File inviati:  `{ok}/{len(media)}`\n"
        f"💾 Dimensione:   `{fmt_mb(total_bytes/1024/1024)}`\n"
        f"📦 Album totali: `{s['total_albums']}`"
    )
    if skipped:
        summary += f"\n⚠️ Saltati: `{skipped}`"
    if q_remaining:
        summary += f"\n\n📋 In coda: `{len(q_remaining)}` album"
    summary += "\n\n*Cosa vuoi fare adesso?*"

    try: await status.delete()
    except Exception: pass

    await query.message.reply_text(
        summary,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_after_download(has_queue=bool(q_remaining)),
        disable_notification=True,  # notifica silenziosa
    )


# ═══════════════════════════════════════════════
#  INVIO ALBUM
# ═══════════════════════════════════════════════
async def _send_album(message, session, media, seller, album_id, status_msg, uid=0):
    ok = 0; total_bytes = 0; batch = []

    async def flush(b):
        nonlocal ok, total_bytes
        tg_media = []
        for i, (data, mtype, fname) in enumerate(b):
            buf = io.BytesIO(data); buf.name = fname
            cap = f"`{fname}`" if i == 0 else None
            if mtype == "video":
                tg_media.append(InputMediaVideo(media=buf, filename=fname, caption=cap,
                                                parse_mode=ParseMode.MARKDOWN, supports_streaming=True))
            else:
                tg_media.append(InputMediaPhoto(media=buf, caption=cap, parse_mode=ParseMode.MARKDOWN))
            ok += 1; total_bytes += len(data)
        try:
            await message.reply_media_group(media=tg_media)
        except Exception:
            for data, mtype, fname in b:
                buf = io.BytesIO(data); buf.name = fname
                try:
                    if mtype == "video": await message.reply_video(video=buf, filename=fname, supports_streaming=True)
                    else: await message.reply_document(document=buf, filename=fname)
                except Exception: pass

    for idx, item in enumerate(media, 1):
        if stop_events.get(uid): break
        url, mtype = item["url"], item["type"]
        fname = make_filename(idx, url, mtype, seller, album_id)
        pct   = int(idx / len(media) * 100)
        try:
            await status_msg.edit_text(
                f"⬇️ *{idx}/{len(media)}* — `{fname}`\n\n"
                f"`{progress_bar(idx, len(media))}` {pct}%\n"
                f"_Premi Stop per interrompere_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_stop(),
            )
        except Exception: pass
        data = download_bytes(session, url, mtype)
        if not data or len(data) > TG_LIMIT: continue
        batch.append((data, mtype, fname))
        if len(batch) == 10:
            await flush(batch); batch = []; time.sleep(0.3)

    if batch: await flush(batch)
    return ok, total_bytes


# ═══════════════════════════════════════════════
#  INVIO ZIP
# ═══════════════════════════════════════════════
async def _send_zip(message, session, media, album_url, seller, album_id, status_msg, uid=0):
    zip_name = f"{seller}_{album_id}.zip"
    buf = io.BytesIO(); ok = 0; total_bytes = 0

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for idx, item in enumerate(media, 1):
            if stop_events.get(uid): break
            url, mtype = item["url"], item["type"]
            fname = make_filename(idx, url, mtype, seller, album_id)
            pct   = int(idx / len(media) * 100)
            try:
                await status_msg.edit_text(
                    f"⬇️ *{idx}/{len(media)}* — `{fname}`\n\n"
                    f"`{progress_bar(idx, len(media))}` {pct}%\n"
                    f"_Premi Stop per interrompere_",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=kb_stop(),
                )
            except Exception: pass
            data = download_bytes(session, url, mtype)
            if data:
                zf.writestr(fname, data); ok += 1; total_bytes += len(data)
            time.sleep(0.15)

    buf.seek(0); zip_bytes = buf.getvalue()
    try:
        await status_msg.edit_text(
            f"📤 Invio `{zip_name}` ({len(zip_bytes)//1024} KB)...",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception: pass

    if len(zip_bytes) <= TG_LIMIT:
        b = io.BytesIO(zip_bytes); b.name = zip_name
        await message.reply_document(document=b, filename=zip_name, caption=f"📦 {ok} file")
    else:
        parts = _split_zip(media, session, seller, album_id)
        for i, (pname, pbuf) in enumerate(parts, 1):
            pbuf.seek(0)
            await message.reply_document(document=pbuf, filename=pname, caption=f"Parte {i}/{len(parts)}")

    return ok, total_bytes

def _split_zip(media, session, seller, album_id):
    MAX = 45 * 1024 * 1024
    parts, part, buf, cur = [], 1, io.BytesIO(), 0
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED)
    for idx, item in enumerate(media, 1):
        data = download_bytes(session, item["url"], item["type"])
        if not data: continue
        fname = make_filename(idx, item["url"], item["type"], seller, album_id)
        if cur + len(data) > MAX and cur > 0:
            zf.close(); parts.append((f"{seller}_{album_id}_part{part}.zip", buf))
            part += 1; buf = io.BytesIO(); zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED); cur = 0
        zf.writestr(fname, data); cur += len(data)
    zf.close()
    if cur > 0: parts.append((f"{seller}_{album_id}_part{part}.zip", buf))
    return parts


# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════
def main():
    if BOT_TOKEN == "METTI_QUI_IL_TUO_TOKEN":
        print("ERRORE: configura YUPOO_BOT_TOKEN"); sys.exit(1)
    print("🤖 Yupoo Bot v4 avviato!")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("annulla", cmd_annulla))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
تحلیلگر اخبار نظامی ایران
تیترهای ۳ روز اخیر را از منابع دسته‌بندی‌شده جمع می‌کند، Gemini (با جست‌وجوی گوگل) مقالات را می‌خواند
و با قواعد ضدفریب یک جمع‌بندی فارسی می‌دهد. هر چند ساعت خودکار تازه می‌شود و (اختیاری) به تلگرام می‌رود.
"""
import os, time, threading, re, requests
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from urllib.parse import quote, unquote, urljoin, urlparse
from html import unescape
from html.parser import HTMLParser
from flask import Flask, jsonify, request, Response

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
USE_SEARCH = os.environ.get("USE_SEARCH") == "1"   # جست‌وجوی گوگل در API فقط در پلن پولی هست
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")          # رمز دکمه «تحلیل تازه»
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")              # اختیاری
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")             # اختیاری
REFRESH_HOURS = float(os.environ.get("REFRESH_HOURS", "8"))
DAYS = int(os.environ.get("DAYS", "3"))

# دامنه: «نوع | گرایش». برچسب‌ها را خودت ویرایش کن؛ همین برچسب‌ها به مدل می‌گویند هر ادعا از کدام طرف آمده.
SOURCES = {
    "understandingwar.org": "اندیشکده | آمریکا، نظامی-محافظه‌کار (ISW)",
    "criticalthreats.org": "اندیشکده | آمریکا، پروژه تهدیدهای ایران",
    "iiss.org": "اندیشکده | بریتانیا، تخصصی نظامی",
    "rusi.org": "اندیشکده | بریتانیا، دفاعی",
    "csis.org": "اندیشکده | آمریکا، میانه",
    "carnegieendowment.org": "اندیشکده | آمریکا، میانه",
    "atlanticcouncil.org": "اندیشکده | آمریکا، اطلسی",
    "crisisgroup.org": "اندیشکده | میانجی‌گرا، میدانی",
    "quincyinst.org": "اندیشکده | آمریکا، ضد مداخله",
    "fdd.org": "اندیشکده | آمریکا، تندرو علیه جمهوری اسلامی",
    "inss.org.il": "اندیشکده | اسرائیل",
    "armscontrol.org": "اندیشکده | کنترل تسلیحات و هسته‌ای",
    "iaea.org": "نهاد رسمی | آژانس بین‌المللی انرژی اتمی",
    "warontherocks.com": "تحلیل نظامی | آمریکا، متخصصان و افسران",
    "breakingdefense.com": "رسانه دفاعی | آمریکا",
    "janes.com": "رسانه دفاعی | بریتانیا، تخصصی",
    "reuters.com": "خبرگزاری | بین‌المللی",
    "apnews.com": "خبرگزاری | بین‌المللی",
    "bbc.com": "رسانه | بریتانیا",
    "aljazeera.com": "رسانه | قطر",
    "al-monitor.com": "رسانه | خاورمیانه",
    "amwaj.media": "رسانه | منطقه، تخصصی ایران",
    "timesofisrael.com": "رسانه | اسرائیل",
    "iranintl.com": "رسانه فارسی‌زبان خارج از کشور | اپوزیسیون",
    "tasnimnews.com": "رسانه دولتی ایران | نزدیک به سپاه",
    "presstv.ir": "رسانه دولتی ایران | روایت رسمی",
    "tehrantimes.com": "رسانه دولتی ایران | روایت رسمی",
    "tass.com": "رسانه دولتی روسیه",
    "globaltimes.cn": "رسانه دولتی چین",
}

QUERY = "Iran (military OR missile OR nuclear OR strike OR IRGC OR drone OR navy OR air defense OR sanctions)"

MODE_SEARCH = ("برای خواندن خود مقالات و گزارش‌ها از جست‌وجوی گوگل استفاده کن "
               "(به‌ویژه اندیشکده‌ها، گزارش‌های تخصصی دفاعی، تصاویر ماهواره‌ای و نهادهای بین‌المللی) و فقط به تیتر اکتفا نکن.")
MODE_HEADLINES = ("تو به اینترنت دسترسی نداری و فقط همین تیترها را داری. هیچ جزئیات یا واقعیتی را از خودت اضافه نکن "
                  "و دربارهٔ رویدادهای اخیر از حافظه‌ات ادعا نساز؛ هرچه از تیترها برنمی‌آید «نامشخص» است.")

PROMPT = """تو تحلیلگر امور نظامی و امنیتی ایران هستی و برای مردم عادیِ فارسی‌زبان «گزارش وضعیت» می‌نویسی: دقیق، بی‌طرف، ساده و بدون اغراق. فقط دربارهٔ ایران. امروز {today} است (به وقت تهران).
ورودی: تیترهای چند روز اخیر، هرکدام با نوع و گرایش منبع. {mode}

قواعد اعتبارسنجی (برای هر ادعا):
۱. «تأییدشده» فقط وقتی که دست‌کم دو منبع از دو خانوادهٔ مستقل گزارش کرده باشند. رسانه‌ای که «به نقل از»، «به گزارش» یا «طبق تحلیل» رسانهٔ دیگر می‌نویسد، یا از یک خبرگزاری گرفته، با همان رسانه یک منبع است؛ اگر از ورودی روشن نیست مستقل‌اند، «تأییدشده» نزن.
۲. ادعای رسانه‌های دولتی ایران، روسیه و چین، مقام‌های اسرائیل و آمریکا، سپاه و ارتش ایران و رسانه‌های اپوزیسیون «ادعای طرفِ X» است، مگر مستقل تأیید شده باشد. گوینده را فقط با آنچه در ورودی آمده بنویس؛ مقام ناشناس را «مقام‌های ناشناس (به نقل از رسانهٔ X)» بنویس، نه «مقام‌های آمریکا».
۳. خبر تک‌منبعی یا با منبع ناشناس، و خبر دربارهٔ نیت، برنامه یا بررسی گزینه‌ها (تصمیم‌های داخلی)، حداکثر «محتمل» است. خبر قدیمی یا بی‌تاریخ را «قدیمی» بزن و فقط اگر به وضع فعلی ربط دارد بیاور.
۴. هرچه از ورودی برنمی‌آید «نامشخص» است؛ حدس را حدس بنام و پیش‌نویس‌ها و پیش‌بینی‌ها را واقعیت جلوه نده. چیزی از خودت اضافه نکن.
۵. نظر تحلیلگر یا کارشناس فقط با نام و وابستگی‌ای که در ورودی آمده بیاید؛ «کارشناسان» بی‌نام ممنوع. گرایش عمومی یک رسانه را جای نظر تحلیلگر ننویس.
۶. نام و عدد برآمده از ترجمهٔ ماشینی (نام آژانس‌ها، شناسه‌ها، رقم‌های دقیق) را فقط وقتی بیاور که معنای روشن دارند.

خروجی را دقیقاً در دو قسمت بنویس.

قسمت اول با خط «=== یادداشت ===» شروع می‌شود (به مخاطب نشان داده نمی‌شود و فقط برای نظم دادن به کار توست): حداکثر ۱۴ خط، هر خط یک ادعا به این شکل:
- وضعیت | گوینده | ادعا | منابع
وضعیت یکی از این‌هاست: تأییدشده، محتمل، ادعای طرفِ X، متناقض، قدیمی. اگر دو بخشِ یک خبر قوت متفاوتی دارند، دو خط بنویس.

قسمت دوم با خط «=== گزارش ===» شروع می‌شود و متن نهایی است:
- ساختار به این ترتیب است: (۱) یک پاراگراف کوتاه، یکی دو جمله، با مهم‌ترین تحول و نتیجهٔ کلی؛ (۲) فهرستی با «- » از خبرهای اصلی؛ (۳) خط «در ۷۲ ساعت آینده باید دید:» و زیرش دو سه مورد با «- »؛ (۴) یک پاراگراف پایانی. حداکثر ۳۵۰ کلمه. بدون سرفصل، بدون جدول، بدون خط جداکننده، و بدون خط خالی میان موارد فهرست.
- فهرست اصلی ۵ تا ۸ مورد دارد؛ هر مورد یک خبر یا ادعای مستقل در یک تا دو جمله است، به ترتیب: میدان نظامی، دریایی و انرژی؛ بعد دیپلماسی و پروندهٔ هسته‌ای. هر مورد یک گوینده و یک وضعیت دارد.
- موارد «۷۲ ساعت آینده» رویدادهای نامطمئن و قابل مشاهده‌اند که پیش‌تر در متن آمده‌اند؛ رویداد قطعی مثل پایان یک انتخابات نشانه نیست.
- هیچ جملهٔ خبری‌ای نباید از بیرون «یادداشت» بیاید. «تأییدشده» را بی‌قید و واقعیت‌وار بنویس؛ «محتمل» را با «احتمالاً» یا «گزارش‌ها می‌گویند، اما تأیید مستقل نشده»؛ ادعای هر طرف را همیشه با نام گوینده («به ادعای سپاه»، «به گفتهٔ مقام‌های ناشناس آمریکایی به نقل از ...»). ادعا را قوی‌تر از وضعیتش نگو.
- پاراگراف پایانی، یکی دو جمله: **سطح اطمینان: زیاد/متوسط/کم** با دلیل کوتاه، و در صورت لزوم مهم‌ترین هشدار (ادعای یک‌طرفه، بازنشر از یک منبع، ناهمخوانی). سطح اطمینان بالاتر از وضعیت مهم‌ترین ادعای متن نباشد: اگر آن «محتمل» یا «ادعای یک طرف» است، حداکثر «متوسط».
- امضا یا توضیح دربارهٔ هوش مصنوعی ننویس؛ سیستم خودش اضافه می‌کند.
- زبان ساده برای مردم عادی: اصطلاح تخصصی را در چند کلمه توضیح بده یا حذف کن؛ جمله‌های کوتاه؛ بدون مقدمه و تعارف.

تیترها:
"""

# ایران از ۱۴۰۱ ساعت تابستانی ندارد و همیشه UTC+3:30 است؛ offset ثابت به tzdata سرور وابسته نیست.
TEHRAN = timezone(timedelta(hours=3, minutes=30))


def tehran_now(fmt="%Y-%m-%d %H:%M"):
    return datetime.now(TEHRAN).strftime(fmt)


STATE = {"text": "", "sources": [], "links": [], "time": "", "n": 0, "error": ""}
LOCK = threading.Lock()


def fetch_domain(domain):
    url = ("https://news.google.com/rss/search?q="
           + quote(f"{QUERY} site:{domain} when:{DAYS}d") + "&hl=en-US&gl=US&ceid=US:en")
    try:
        root = ET.fromstring(requests.get(url, timeout=20).content)
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        out.append({"d": domain, "t": it.findtext("title") or "", "date": (it.findtext("pubDate") or "")[:16]})
        if len(out) >= 6:
            break
    return out


def collect():
    with ThreadPoolExecutor(8) as ex:
        return [i for r in ex.map(fetch_domain, SOURCES) for i in r]


FALLBACKS = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]   # اگر مدل اصلی شلوغ یا در دسترس نبود
RETRYABLE = (429, 500, 503, 504)


GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "150"))   # ثانیه؛ صبر برای پاسخ هر درخواست
GEMINI_BUDGET = int(os.environ.get("GEMINI_BUDGET", "540"))     # سقف کل زمان برای همهٔ مدل‌ها و تلاش‌ها


def call_model(model, body, deadline):
    """تا سه بار تلاش می‌کند. 429/5xx و قطعی شبکه را موقتی می‌داند و با فاصله دوباره می‌زند؛
    اما اگر مدل در مهلت جواب نداد (Timeout)، تکرار روی همان مدل بی‌فایده است و فوراً برمی‌گردد
    تا مدل بعدی امتحان شود. خروجی: (آخرین پاسخ یا None، آخرین خطای شبکه یا None)."""
    r, err = None, None
    for wait in (0, 10, 30):
        if time.time() + wait > deadline - 10:
            break
        time.sleep(wait)
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": GEMINI_KEY}, json=body,
                timeout=(10, max(10, min(GEMINI_TIMEOUT, deadline - time.time()))),
            )
        except requests.exceptions.Timeout as e:
            err = f"timeout: {e!r}"[:200]
            print(f"gemini: {model} timed out")
            break
        except requests.exceptions.RequestException as e:
            err = repr(e)[:200]
            print(f"gemini: {model} network error: {err}")
            continue
        r = resp
        if r.status_code not in RETRYABLE:
            return r, None
    return r, err


def parse_reply(r):
    data = r.json()
    if not data.get("candidates"):
        raise RuntimeError("Gemini پاسخی نداد: " + str(data.get("promptFeedback")))
    c = data["candidates"][0]
    text = "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))
    if not text:
        raise RuntimeError("پاسخ خالی از Gemini: " + str(c.get("finishReason")))
    chunks = c.get("groundingMetadata", {}).get("groundingChunks", [])
    return text, sorted({ch["web"].get("title", "") for ch in chunks if "web" in ch} - {""})


def ask_gemini(prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if USE_SEARCH:
        body["tools"] = [{"google_search": {}}]
    deadline = time.time() + GEMINI_BUDGET
    last = "هیچ تلاشی انجام نشد (مهلت کل تمام شد)"
    for model in [MODEL] + [m for m in FALLBACKS if m != MODEL]:
        if time.time() > deadline - 20:
            break
        r, err = call_model(model, body, deadline)
        if r is not None and r.status_code == 200:
            return parse_reply(r)
        if r is not None:
            last = f"{model} -> {r.status_code}: {r.text[:300]}"
            if r.status_code != 404 and r.status_code not in RETRYABLE:
                break                             # مثلاً 400 یا 403: مشکل کلید/درخواست است، مدل دیگر کمکی نمی‌کند
        else:
            last = f"{model} -> {err}"
        print("gemini: falling back after:", last[:200])
    raise RuntimeError("Gemini: " + last)


def tg_html(md):
    """مارک‌داونِ ساده -> HTML تلگرام (تلگرام خودش ## و ** را نمی‌فهمد)."""
    out = []
    for line in md.splitlines():
        line = line.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        if line.startswith("## "):
            line = "\n<b>■ " + re.sub(r"</?b>", "", line[3:]) + "</b>"
        elif line[:2] in ("- ", "* "):
            line = "• " + line[2:]
        out.append(line)
    return "\n".join(out)


AI_BYLINE = os.environ.get(
    "AI_BYLINE", "🤖 این گزارش را هوش مصنوعی نوشته است")
HASHTAGS = os.environ.get("TG_HASHTAGS", "#گزارش_وضعیت #ایران")   # آخر آخرین پیام؛ برای پیدا کردن گزارش‌ها لابه‌لای پیام‌ها


TG_LIMIT = 3900          # سقف تلگرام ۴۰۹۶ حرف است؛ کمی کمتر می‌گیریم چون تگ‌های HTML هم شمرده می‌شوند


def telegram(md, header="", footer=""):
    """گزارش یک متن پاراگرافی است و معمولاً در یک پیام جا می‌شود؛ فقط اگر از سقف تلگرام بلندتر شد،
    در مرز پاراگراف به چند پیام تقسیم می‌شود. پانویس و هشتگ‌ها همیشه آخر آخرین پیام‌اند."""
    if not (TG_TOKEN and TG_CHAT):
        return
    paras = [header] + re.split(r"\n\s*\n", tg_html(md)) + [footer, AI_BYLINE, HASHTAGS]
    chunks, cur = [], ""
    for x in (p.strip() for p in paras if p and p.strip()):
        lines = x.split("\n") if len(x) > TG_LIMIT else [x]                   # بلاک بلند: اول در مرز خط
        for p in (y[i:i + TG_LIMIT] for y in lines for i in range(0, max(len(y), 1), TG_LIMIT)):   # خط خیلی بلند: برش اجباری
            if cur and len(cur) + len(p) + 2 > TG_LIMIT:
                chunks.append(cur)
                cur = ""
            cur = f"{cur}\n\n{p}" if cur else p
    chunks.append(cur)
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for c in chunks:
        try:
            r = requests.post(url, json={"chat_id": TG_CHAT, "text": c.strip(), "parse_mode": "HTML",
                                         "disable_web_page_preview": True}, timeout=20)
            if r.status_code == 400:                  # اگر تگی خراب بود، بدون قالب بفرست
                plain = unescape(re.sub(r"<[^>]+>", "", c)).strip()
                r = requests.post(url, json={"chat_id": TG_CHAT, "text": plain}, timeout=20)
            if r.status_code != 200:
                print("telegram:", r.status_code, r.text[:200])
        except Exception as e:
            print("telegram failed:", repr(e))


# ---------- جام جمشید (jjs.pub): ترجمهٔ فارسی خبرهای رسانه‌های خارجی ----------
JJS = "https://jjs.pub"
JJS_UA = {"User-Agent": "Mozilla/5.0 (personal news-analysis script)"}
JJS_MAX = int(os.environ.get("JJS_MAX", "20"))               # حداکثر خبر در هر اجرا
JJS_ITEM_MAX = int(os.environ.get("JJS_ITEM_MAX", "30000"))  # سقف ایمنی برای یک خبر (فقط صفحه‌های غیرعادی را کوتاه می‌کند)
JJS_TOTAL = int(os.environ.get("JJS_TOTAL", "120000"))       # سقف مجموع حرف خبرها در یک درخواست Gemini؛ خبر نیمه‌کاره نمی‌رود
IRAN_WORDS = ("ایران", "تهران", "سپاه", "هرمز", "خامنه", "پزشکیان")
OUTLETS = {  # رسانهٔ اصلی خبر (از خودِ صفحهٔ جام جمشید خوانده می‌شود) -> نوع | گرایش
    "reuters": "خبرگزاری | بین‌المللی", "apnews": "خبرگزاری | بین‌المللی",
    "bloomberg": "رسانه اقتصادی | آمریکا", "wsj": "رسانه اقتصادی | آمریکا",
    "nytimes": "رسانه | آمریکا", "theatlantic": "رسانه | آمریکا", "ft.com": "رسانه اقتصادی | بریتانیا",
    "bbc": "رسانه | بریتانیا", "foxnews": "رسانه | آمریکا، محافظه‌کار",
    "theamericanconservative": "رسانه | آمریکا، محافظه‌کار ضدمداخله",
    "jpost": "رسانه | اسرائیل", "maariv": "رسانه | اسرائیل", "timesofisrael": "رسانه | اسرائیل",
    "israelnationalnews": "رسانه | اسرائیل، راست‌گرا", "jns": "رسانه | اسرائیل/آمریکا، هوادار اسرائیل",
    "newarab": "رسانه | قطر/منطقه", "aljazeera": "رسانه | قطر",
    "moderndiplomacy": "رسانه تحلیلی | بین‌المللی",
}
JJS_INTRO = """

متن خبرهای «جام جمشید» (jjs.pub):
این‌ها ترجمه و بازنویسی ماشینی خبرِ یک رسانهٔ خارجی‌اند و منبع دست‌دوم حساب می‌شوند. هر خبر را به رسانهٔ اصلی‌اش نسبت بده
(«به نقل از X، از طریق جام جمشید») و نوع و گرایش همان رسانه را در نظر بگیر. جام جمشید و رسانهٔ اصلی یک منبع‌اند، نه دو منبع مستقل؛
اگر همان خبر در فهرست تیترهای بالا هم از همان رسانه آمده، آن را دوبار نشمار. چون ترجمهٔ ماشینی است، اسم‌ها و عددها را با احتیاط بیاور.
"""
MODE_TEXTS = ("تو به اینترنت دسترسی نداری و فقط همین تیترها و متن چند خبر را داری. هیچ جزئیات یا واقعیتی را از خودت اضافه نکن "
              "و دربارهٔ رویدادهای اخیر از حافظه‌ات ادعا نساز؛ هرچه از این‌ها برنمی‌آید «نامشخص» است.")


def label_for(orig):
    o = orig.lower()
    return next((v for k, v in OUTLETS.items() if k in o), "نوع و گرایش نامشخص")


class _Paras(HTMLParser):                          # متن پاراگراف‌های صفحه را جمع می‌کند
    def __init__(self):
        super().__init__()
        self.in_p, self.skip, self.cur, self.paras = 0, 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "footer", "nav"):
            self.skip += 1
        elif tag == "p":
            self.in_p, self.cur = 1, []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "footer", "nav"):
            self.skip = max(0, self.skip - 1)
        elif tag == "p" and self.in_p:
            self.in_p = 0
            t = " ".join("".join(self.cur).split())
            if len(t) > 40:
                self.paras.append(t)

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.cur.append(data)


def page_text(h):
    p = _Paras()
    p.feed(h)
    text = " ".join(p.paras)
    if len(text) < 400:                            # اگر صفحه با <p> نوشته نشده بود
        body = re.sub(r"(?s)<(script|style|footer|nav).*?</\1>", " ", h)
        text = " ".join(unescape(re.sub(r"<[^>]+>", " ", body)).split())
        i = text.find("در یک نگاه")
        text = text[i:] if i >= 0 else text
    return text


def _norm(x):
    """ي و ك عربی را به ی و ک فارسی تبدیل می‌کند (اسلاگ‌های جام جمشید هر دو را قاطی دارند)."""
    return x.replace("\u064a", "\u06cc").replace("\u0643", "\u06a9")


def _strip_tags(x):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", x)).split())


def jjs_candidates(html):
    """همهٔ لینک‌های هم‌دامنه را همراه با متن لینک می‌خواند و آن‌هایی را نگه می‌دارد که شناسهٔ عددی دارند
    و در آدرس یا متنشان کلمهٔ مربوط به ایران هست. به شکل آدرس (نقل‌قول تکی/دوتایی، لینک نسبی/مطلق،
    با یا بدون اسلاگ و اسلش پایانی) وابسته نیست."""
    seen, urls = set(), []
    for m in re.finditer(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", html, re.S | re.I):
        href, text = unescape(m.group(1)).strip(), _strip_tags(m.group(2))
        full = urljoin(JJS + "/last24", href).split("#")[0]
        p = urlparse(full)
        if p.netloc.replace("www.", "") != "jjs.pub" or p.path.strip("/") in ("", "last24"):
            continue
        if not re.search(r"\d{3,}", p.path):                      # خبر شناسهٔ عددی دارد
            continue
        seen.add(full)
        if full not in urls and any(_norm(w) in _norm(unquote(full) + " " + text) for w in IRAN_WORDS):
            urls.append(full)
    return seen, urls


def jjs_debug(html):
    hrefs = re.findall(r"""href\s*=\s*["']([^"']+)["']""", html, re.I)
    pages = [h for h in hrefs if not re.search(r"\.(css|js|png|jpe?g|svg|ico|webp|woff2?)(\?|$)", h, re.I)]
    shapes = Counter("/" + (urlparse(urljoin(JJS + "/last24", h)).path.split("/")[1:2] or [""])[0][:15] for h in pages)
    print(f"jjs[debug]: <a> tags={html.count('<a ')} hrefs={len(hrefs)} '/content/'={html.count('/content/')}"
          f" next_data={'__NEXT_DATA__' in html}")
    print("jjs[debug]: first path segments:", shapes.most_common(8))
    print("jjs[debug]: sample hrefs:", [unquote(h)[:70] for h in pages[:15]])
    print("jjs[debug]: rss/api hints:", re.findall(r"""<link[^>]+(?:rss|atom)[^>]*>""", html, re.I)[:2],
          re.findall(r"""["'](/(?:api|_next/data|feed|rss)[^"']{0,60})""", html)[:5])


def fetch_jjs():
    """لینک‌های jjs.pub/last24 را برمی‌دارد، خبرهای مربوط به ایران را یکی‌یکی می‌خواند."""
    r = requests.get(f"{JJS}/last24", headers=JJS_UA, timeout=25)
    print(f"jjs: last24 status={r.status_code} bytes={len(r.text)}")
    r.raise_for_status()
    seen, urls = jjs_candidates(r.text)
    print(f"jjs: {len(seen)} links found, {len(urls)} about Iran")
    if not urls:
        jjs_debug(r.text)            # الگوی لینک‌ها را در لاگ نشان می‌دهد تا بشود فیلتر را درست کرد
    out, total, cut, skipped = [], 0, 0, 0
    batch = urls[:JJS_MAX]
    for idx, u in enumerate(batch):
        time.sleep(1)                                         # مؤدبانه: هر ثانیه یک صفحه
        try:
            resp = requests.get(u, headers=JJS_UA, timeout=25)
        except Exception as e:
            print("jjs: page failed:", repr(e))
            continue
        if resp.status_code != 200:
            print("jjs: page status", resp.status_code)
            continue
        h = resp.text
        t = re.search(r"<title>(.*?)</title>", h, re.S)
        title = unescape(t.group(1)).split("|")[0].strip() if t else unquote(u.rsplit("/", 1)[-1]).replace("-", " ")
        o = re.search(r'منبع اصلی[^<]*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', h)
        orig_url, orig = (unescape(o.group(1)), unescape(o.group(2)).strip()) if o else ("", "")
        if not orig:
            m2 = re.search(r"منتشر شده در:\s*(?:<[^>]+>\s*)*([A-Za-z0-9.\-]+)", h)
            orig = m2.group(1) if m2 else "نامشخص"
        d = re.search(r'published_time[^>]*content="([^"]+)"', h) or re.search(r'content="([^"]+)"[^>]*published_time', h)
        text = page_text(h)
        if len(text) <= 150:
            print("jjs: page text too short:", len(text))
        else:
            was_cut = len(text) > JJS_ITEM_MAX
            text = text[:JJS_ITEM_MAX]
            if out and total + len(text) > JJS_TOTAL:           # خبر را نیمه‌کاره نمی‌فرستیم؛ همین‌جا می‌ایستیم
                skipped += len(batch) - idx
                print(f"jjs: input budget reached ({total} chars); {skipped} item(s) not read")
                break
            total += len(text)
            cut += was_cut
            print(f"jjs: item {len(out) + 1}: {len(text)} chars{' (CUT)' if was_cut else ''} ... {text[-50:]}")
            out.append({"title": title, "orig": orig, "orig_url": orig_url, "date": d.group(1)[:10] if d else "", "text": text})
    print(f"jjs: kept {len(out)} items, {total} chars total")
    return out, {"cut": cut, "skipped": skipped + max(0, len(urls) - JJS_MAX)}


REPORT_RE = re.compile(r"=+\s*گزارش\s*=+")


def extract_report(raw):
    """«یادداشت» (قسمت اول خروجی مدل) فقط برای نظم‌دادن مدل است و حذف می‌شود؛ فقط متن بعد از «=== گزارش ===» می‌ماند."""
    m = list(REPORT_RE.finditer(raw))
    if m:
        raw = raw[m[-1].end():]
    else:
        print("gemini: report marker missing; stripping note lines")
        raw = re.sub(r"(?m)^(=+.*|- .*)$", "", raw)
    raw = re.sub(r"(?m)^#+\s*", "", raw)                      # سرفصل ناخواسته
    raw = re.sub(r"(?m)^\* ", "- ", raw)
    raw = re.sub(r"(?m)^(- .*)\n\s*\n(?=- )", r"\1\n", raw)     # خط خالی میان موارد فهرست حذف شود
    raw = re.sub(r"(?m)^(- .*)\n(?=[^-\n])", r"\1\n\n", raw)          # بعد از فهرست، متن بعدی با یک خط خالی جدا شود
    return re.sub(r"\n{3,}", "\n\n", raw).strip()


def limits_text(n_src, n_jjs):
    """فقط تعداد منابع خوانده‌شده: سایت‌هایی که تیترشان آمده + خبرهای جام جمشید که متنشان خوانده شده."""
    return f"تعداد منابع خوانده‌شده: {n_src + n_jjs}"


def build_report():
    items = collect()
    try:
        jjs, jstats = fetch_jjs()
    except Exception as e:                         # خرابی جام جمشید نباید کل گزارش را بخواباند
        jjs, jstats = [], {}
        print("jjs.pub failed:", repr(e))
    if not items and not jjs:
        raise RuntimeError("هیچ خبری پیدا نشد؛ اینترنت سرور یا Google News را بررسی کن.")
    lines = "\n".join(f"- [{SOURCES[i['d']]}] {i['d']} — {i['t']} ({i['date']})" for i in items) or "(تیتری پیدا نشد)"
    extra = ""
    if jjs:
        extra = JJS_INTRO + "\n".join(
            f"\n[{k}] {j['title']}\nرسانهٔ اصلی: {j['orig']} ({label_for(j['orig'])}) | تاریخ: {j['date']}\nمتن: {j['text']}"
            for k, j in enumerate(jjs, 1))
    mode = MODE_SEARCH if USE_SEARCH else (MODE_TEXTS if jjs else MODE_HEADLINES)
    raw, srcs = ask_gemini(PROMPT.format(today=tehran_now("%Y-%m-%d"), mode=mode) + lines + extra)
    text = extract_report(raw)
    if not text:
        raise RuntimeError("مدل گزارش خالی برگرداند.")
    print(f"report: {len(text)} chars, {len(text.split())} words")
    srcs = srcs or sorted({i["d"] for i in items})
    if jjs:
        srcs = ["jjs.pub (جام جمشید)"] + srcs
    links = [{"t": j["title"], "o": j["orig"], "u": j["orig_url"]} for j in jjs if j["orig_url"].startswith("http")]
    stamp = tehran_now() + " به وقت تهران"
    with LOCK:
        STATE.update(text=text, sources=srcs, links=links, n=len(items) + len(jjs), error="", time=stamp)
    footer = limits_text(len({i["d"] for i in items}), len(jjs)) + ("\nمنبع بخشی از خبرها: جام جمشید (jjs.pub)" if jjs else "")
    telegram(text, f"<b>گزارش وضعیت</b> · {stamp}", footer)


def loop():
    while True:
        try:
            build_report()
        except Exception as e:
            STATE["error"] = str(e)
        time.sleep(REFRESH_HOURS * 3600)


PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>تحلیل نظامی ایران</title>
<style>
:root{--bg:#eef1f4;--ink:#1c2530;--mute:#5d6b7a;--acc:#0f5c6e;--card:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#141a21;--ink:#e3e9ef;--mute:#92a0ae;--acc:#5fb3c7;--card:#1c242d}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.9 Tahoma,"Segoe UI",sans-serif}
main{max-width:46rem;margin:0 auto;padding:1.5rem 1rem 3rem}
header{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
h1{font-size:1.3rem;margin:0}
button{font:inherit;background:var(--acc);color:var(--card);border:0;border-radius:6px;padding:.4rem 1rem;cursor:pointer}
button:disabled{opacity:.6}button:focus-visible{outline:3px solid var(--ink);outline-offset:2px}
#m,#s,#n,#c{color:var(--mute);font-size:.85rem}
#r{background:var(--card);border-radius:8px;padding:.5rem 1.25rem 1rem;margin-top:1rem}
h2{font-size:1.05rem;color:var(--acc);margin:1.5rem 0 .2rem}
#r>p:first-child{font-size:1.1rem;border-right:4px solid var(--acc);padding-right:.9rem}
p{margin:.4rem 0}li{list-style:disc inside;margin:.35rem .4rem}
#l{margin-top:1rem;font-size:.9rem}#l a{color:var(--acc)}\n</style></head><body><main>
<header><h1>تحلیل نظامی ایران</h1><button id="b" onclick="refresh()">تحلیل تازه</button></header>
<div id="m"></div><div id="r"><p>در حال بارگذاری…</p></div>
<div id="l"></div><p id="s"></p>
<p id="c"></p><p id="n">این گزارش را یک عامل هوش مصنوعی نوشته و ممکن است اشتباه کند؛ ادعاهای کلیدی را با منبع اصلی تطبیق بده.</p>
</main><script>
const $=s=>document.querySelector(s);
function md(t){return t.split('\\n').map(l=>{
 l=l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>');
 if(l.startsWith('## '))return '<h2>'+l.slice(3)+'</h2>';
 if(/^[-*] /.test(l))return '<li>'+l.slice(2)+'</li>';
 return l.trim()?'<p>'+l+'</p>':''}).join('')}
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
function render2(d){
 const L=d.links||[];
 $('#l').innerHTML=L.length?'<h2>منابع اصلی خبرهای جام جمشید</h2><ul>'+L.map(x=>'<li><a href="'+esc(x.u)+'" target="_blank" rel="noopener">'+esc(x.t)+'</a> — '+esc(x.o)+'</li>').join('')+'</ul>':'';
 $('#c').textContent=(d.sources||[]).some(s=>s.startsWith('jjs.pub'))?'بخشی از خبرهای این گزارش از «جام جمشید» (jjs.pub) گرفته شده؛ با سپاس از ترجمه و گردآوری آن‌ها. متن خبرها بازنشر نشده و فقط تحلیل ماشینی اینجاست.':''}
function show(d){
 if(!d.text){$('#r').innerHTML='<p>'+(d.error||'هنوز گزارشی ساخته نشده؛ چند دقیقه صبر کن یا «تحلیل تازه» را بزن.')+'</p>';return}
 $('#r').innerHTML=md(d.text);
 $('#m').textContent='به‌روزرسانی: '+d.time+' — '+d.n+' تیتر از منابع مختلف';
 render2(d);$('#s').textContent='منابع این گزارش: '+(d.sources||[]).join('، ')}
async function load(){show(await (await fetch('/api/report')).json())}
async function refresh(){
 const b=$('#b');b.disabled=true;b.textContent='در حال تحلیل…';
 try{const r=await fetch('/api/refresh',{method:'POST',headers:{'X-Token':localStorage.tk||''}});
  if(r.status==403)localStorage.tk=prompt('رمز را وارد کن')||'';else show(await r.json())}
 finally{b.disabled=false;b.textContent='تحلیل تازه'}}
load();
</script></body></html>"""

app = Flask(__name__)


@app.get("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.get("/api/report")
def get_report():
    return jsonify(STATE)


@app.post("/api/refresh")
def refresh():
    if ADMIN_TOKEN and request.headers.get("X-Token") != ADMIN_TOKEN:
        return jsonify(error="forbidden"), 403
    try:
        build_report()
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(STATE)


if not os.environ.get("NO_LOOP"):          # report.py (GitHub Actions) sets NO_LOOP=1
    threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

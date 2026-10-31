# -*- coding: utf-8 -*-
"""
تحلیلگر اخبار نظامی ایران
تیترهای ۳ روز اخیر را از منابع دسته‌بندی‌شده جمع می‌کند، Gemini (با جست‌وجوی گوگل) مقالات را می‌خواند
و با قواعد ضدفریب یک جمع‌بندی فارسی می‌دهد. هر چند ساعت خودکار تازه می‌شود و (اختیاری) به تلگرام می‌رود.
"""
import os, time, threading, requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
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
                  "و دربارهٔ رویدادهای اخیر از حافظه‌ات ادعا نساز؛ هرچه از تیترها برنمی‌آید «نامشخص» است. "
                  "در پایانِ جمع‌بندی یک جمله بنویس که این تحلیل فقط بر پایهٔ تیترهاست و متن مقالات خوانده نشده.")

PROMPT = """تو تحلیلگر ارشد امور نظامی و امنیتی هستی و فقط درباره ایران تحلیل می‌کنی. امروز {today} است.
ورودی: تیترهای چند روز اخیر، هرکدام با نوع و گرایش منبع.
{mode}

قواعد ضدفریب:
۱. ادعا را از واقعیت جدا کن. چیزی را «تأییدشده» بنویس فقط اگر دست‌کم دو منبع از دو خانوادهٔ مستقل گزارش کرده باشند
   (مثلاً اندیشکده غربی + رسانه منطقه‌ای، یا تصویر ماهواره‌ای/نهاد بین‌المللی).
۲. ادعای رسانه‌های دولتی ایران، روسیه و چین، مقام‌های اسرائیل و آمریکا، و رسانه‌های اپوزیسیون را همیشه «ادعای طرف X» بنویس،
   مگر اینکه مستقل تأیید شده باشد.
۳. منبع ناشناس، خبر تک‌منبعی، یا خبری که چند سایت از یک منبع واحد بازنشر کرده‌اند را مستقل حساب نکن.
۴. به تاریخ دقت کن؛ خبر قدیمی، ویدیوی بازنشرشده یا تصویر بی‌تاریخ را علامت بزن.
۵. نظر تحلیلگران و افسران بازنشسته را با نام و وابستگی‌شان بیاور؛ اجماع و اختلاف را جدا کن.
۶. هرجا مطمئن نیستی بنویس «نامشخص». حدس را حدس بنام. پیش‌بینی را واقعیت جلوه نده.

خروجی را فقط به فارسی و با همین سرفصل‌ها (مارک‌داون) بنویس:
## جمع‌بندی نهایی
(۳ تا ۵ جمله، و در پایانش یک خط «سطح اطمینان: کم/متوسط/زیاد» با دلیل کوتاه)
## تأییدشده‌ها
## ادعاهای تأییدنشده یا تبلیغاتی
(هر مورد با نام منبع و طرف)
## اجماع تحلیلگران
## اختلاف‌نظر تحلیلگران
## چه چیزی را باید دنبال کرد
(۳ تا ۵ مورد)

تیترها:
"""

STATE = {"text": "", "sources": [], "time": "", "n": 0, "error": ""}
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


def ask_gemini(prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if USE_SEARCH:
        body["tools"] = [{"google_search": {}}]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        params={"key": GEMINI_KEY}, json=body, timeout=240,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:400]}")
    data = r.json()
    if not data.get("candidates"):
        raise RuntimeError("Gemini پاسخی نداد: " + str(data.get("promptFeedback")))
    c = data["candidates"][0]
    text = "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))
    if not text:
        raise RuntimeError("پاسخ خالی از Gemini: " + str(c.get("finishReason")))
    chunks = c.get("groundingMetadata", {}).get("groundingChunks", [])
    return text, sorted({ch["web"].get("title", "") for ch in chunks if "web" in ch} - {""})


def telegram(text):
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": text[:4000]}, timeout=20)
        except Exception:
            pass


def build_report():
    items = collect()
    if not items:
        raise RuntimeError("هیچ تیتری پیدا نشد؛ اینترنت سرور یا Google News را بررسی کن.")
    lines = "\n".join(f"- [{SOURCES[i['d']]}] {i['d']} — {i['t']} ({i['date']})" for i in items)
    mode = MODE_SEARCH if USE_SEARCH else MODE_HEADLINES
    text, srcs = ask_gemini(PROMPT.format(today=time.strftime("%Y-%m-%d"), mode=mode) + lines)
    srcs = srcs or sorted({i["d"] for i in items})
    with LOCK:
        STATE.update(text=text, sources=srcs, n=len(items), error="",
                     time=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
    telegram(text)


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
#m,#s,#n{color:var(--mute);font-size:.85rem}
#r{background:var(--card);border-radius:8px;padding:.5rem 1.25rem 1rem;margin-top:1rem}
h2{font-size:1.05rem;color:var(--acc);margin:1.5rem 0 .2rem}
h2:first-of-type+p{font-size:1.1rem;border-right:4px solid var(--acc);padding-right:.9rem}
p{margin:.4rem 0}li{list-style:disc inside;margin:.35rem .4rem}
</style></head><body><main>
<header><h1>تحلیل نظامی ایران</h1><button id="b" onclick="refresh()">تحلیل تازه</button></header>
<div id="m"></div><div id="r"><p>در حال بارگذاری…</p></div>
<p id="s"></p>
<p id="n">این خلاصه ابزار کمکی است و ممکن است اشتباه کند؛ ادعاهای کلیدی را با منبع اصلی تطبیق بده.</p>
</main><script>
const $=s=>document.querySelector(s);
function md(t){return t.split('\\n').map(l=>{
 l=l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>');
 if(l.startsWith('## '))return '<h2>'+l.slice(3)+'</h2>';
 if(/^[-*] /.test(l))return '<li>'+l.slice(2)+'</li>';
 return l.trim()?'<p>'+l+'</p>':''}).join('')}
function show(d){
 if(!d.text){$('#r').innerHTML='<p>'+(d.error||'هنوز گزارشی ساخته نشده؛ چند دقیقه صبر کن یا «تحلیل تازه» را بزن.')+'</p>';return}
 $('#r').innerHTML=md(d.text);
 $('#m').textContent='به‌روزرسانی: '+d.time+' — '+d.n+' تیتر از منابع مختلف';
 $('#s').textContent='منابع این گزارش: '+(d.sources||[]).join('، ')}
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

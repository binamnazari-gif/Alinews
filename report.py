# -*- coding: utf-8 -*-
"""یک‌بار اجرا (برای GitHub Actions): گزارش را می‌سازد و صفحه‌ی ثابت docs/index.html را می‌نویسد."""
import os, json
os.environ["NO_LOOP"] = "1"
import app

app.build_report()                       # تیترها + تحلیل Gemini + (اختیاری) تلگرام
os.makedirs("docs", exist_ok=True)
page = app.PAGE.replace("/api/report", "report.json").replace(
    '<button id="b" onclick="refresh()">تحلیل تازه</button>', "").replace(
    "<head>", '<head><meta name="robots" content="noindex,nofollow">', 1)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
with open("docs/report.json", "w", encoding="utf-8") as f:
    json.dump(app.STATE, f, ensure_ascii=False)

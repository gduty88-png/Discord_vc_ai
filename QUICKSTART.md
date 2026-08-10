# تشغيل سريع — 5 خطوات لازم تسويها إنت بنفسك

هذي الخطوات محتاجة حسابك الشخصي، ما أقدر أسويها نيابة عنك. الكود جاهز بهذا المجلد.

## 1. توكن الديسكورد
Discord Developer Portal → New Application → Bot → Reset Token
فعّل: Message Content Intent + Server Members Intent
OAuth2 → URL Generator → bot + applications.commands → صلاحيات Connect/Speak/Send Messages → افتح الرابط وضيف البوت لسيرفرك

## 2. مفتاح Groq (مجاني)
console.groq.com → API Keys → Create Key

## 3. صوت الشخصية عبر Colab
افتح Colab جديد → Runtime → T4 GPU → انسخ خلايا `colab_server.py` بالترتيب
سجل بـ ngrok.com وحط توكنك بالخلية المخصصة
ارفع مقطع صوت الشخصية (5-15 ثانية، نظيف بدون موسيقى/ضوضاء)
حط النص الفعلي اللي يقوله بالمقطع في `--ref_text` (يرفع الدقة كثير عن تركه فاضي)
آخر خلية تطبع رابط ينتهي بـ `/tts` — هذا `COLAB_TTS_URL`

## 4. تشغيل تجريبي على جهازك
```bash
pip install -r requirements.txt
cp _env.example .env
# افتح .env واملأ DISCORD_TOKEN و GROQ_API_KEY و COLAB_TTS_URL
python bot.py
```
جرب `/join` بروم صوتي.

## 5. النشر (Render أو Koyeb)
- ارفع المجلد على GitHub (repo خاص، وتأكد `.env` غير مرفوع)
- بالمنصة: اربط الريبو → Build: `pip install -r requirements.txt` → Start: `python bot.py`
- ضيف نفس 3 المتغيرات يدوياً بقسم Environment Variables بالداشبورد
- الكود فيه سيرفر HTTP صغير مدمج (`PORT` تلقائي) عشان يشتغل حتى لو المنصة تشترط بورت مفتوح

## قبل كل جلسة لعب
1. شغّل Colab من جديد → خذ رابط ngrok الجديد
2. حدّثه بمتغيرات المنصة (COLAB_TTS_URL)
3. أعد تشغيل الخدمة

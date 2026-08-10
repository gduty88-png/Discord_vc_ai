# ============================================================
# هذا الكود يتنسخ ويشتغل داخل Google Colab (مو على جهازك أو Render)
# كل خلية (# %% كتلة) تنحط في خلية منفصلة داخل Colab
# ============================================================

# %% [الخلية 1] - تفعيل GPU
# روح من فوق: Runtime > Change runtime type > اختر T4 GPU

# %% [الخلية 2] - تثبيت المكتبات المطلوبة
"""
!pip install f5-tts flask pyngrok soundfile -q
"""

# %% [الخلية 3] - رفع ملف صوت الشخصية الكرتونية (المرجع)
"""
from google.colab import files
print("ارفع ملف صوت الشخصية (wav أو mp3، ٥-١٥ ثانية كافية):")
uploaded = files.upload()
REFERENCE_AUDIO = list(uploaded.keys())[0]
"""

# %% [الخلية 4] - تشغيل سيرفر F5-TTS + ngrok
"""
import subprocess
from flask import Flask, request, send_file
from pyngrok import ngrok
import io

# --- ضع توكن ngrok المجاني (من ngrok.com بعد تسجيل حساب مجاني) ---
NGROK_AUTH_TOKEN = "ضع_التوكن_هنا"
ngrok.set_auth_token(NGROK_AUTH_TOKEN)

app = Flask(__name__)

@app.route("/tts", methods=["POST"])
def tts():
    data = request.get_json()
    text = data.get("text", "")
    if not text:
        return {"error": "no text provided"}, 400

    output_path = "/content/output.wav"

    # يستدعي F5-TTS عن طريق سطر الأوامر مع صوت الشخصية كمرجع
    subprocess.run([
        "f5-tts_infer-cli",
        "--model", "F5TTS_v1_Base",
        "--ref_audio", REFERENCE_AUDIO,
        "--ref_text", "",  # يقدر يفرغه ويخليه يتعرف تلقائي
        "--gen_text", text,
        "--output_file", output_path,
    ], check=True)

    return send_file(output_path, mimetype="audio/wav")


# نشغل نفق ngrok عشان يصير عندنا رابط عام (public URL)
public_url = ngrok.connect(5000)
print("🔗 انسخ هذا الرابط وحطه في COLAB_TTS_URL بملف .env عندك:")
print(f"{public_url}/tts")

app.run(port=5000)
"""

# ============================================================
# ملاحظات مهمة:
# 1. هذا السيرفر يتوقف إذا سكرت تبويب Colab أو انقطع الاتصال.
# 2. كل مرة تشغله من جديد، بيتغير رابط ngrok - لازم تحدّث
#    ملف .env عند البوت (COLAB_TTS_URL) بالرابط الجديد.
# 3. الجلسة المجانية بـ Colab محدودة (عادة لين ١٢ ساعة أو أقل
#    لو ما فيه نشاط مستمر).
# ============================================================

"""
بوت ديسكورد صوتي - يدخل الروم الصوتي ويرد بصوت مستنسخ على رسائل تكتبها بالتشات.

خط سير العمل (Pipeline):
  1. تكتب أمر /say وتحط النص اللي تبي البوت يرد عليه أو يقوله.
  2. يرسل النص لنموذج Groq (LLM مجاني وسريع) مع شخصية البوت.
  3. يرسل رد النص لسيرفر Colab (F5-TTS) عشان يرجع صوت مستنسخ.
  4. يشغل الصوت في الروم الصوتي اللي البوت داخله.

ملاحظة: تعطيل الاستماع/التسجيل الصوتي التلقائي مؤقتاً بسبب قيد حالي
بمكتبة py-cord (استقبال الصوت مكسور بسبب بروتوكول DAVE من ديسكورد،
راجع: https://github.com/Pycord-Development/pycord/issues/3139).
البوت الحين يعتمد على النص المكتوب بدل الاستماع المباشر.
"""

import asyncio
import os
import time
from collections import defaultdict

import discord
import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ------------------ سيرفر صغير بس عشان منصات الاستضافة (Koyeb/Render) ------------------
def _start_keepalive_server():
    import http.server
    import threading

    port = int(os.getenv("PORT", "8000"))

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


_start_keepalive_server()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
COLAB_TTS_URL = os.getenv("COLAB_TTS_URL")  # مثال: https://xxxx.ngrok-free.app/tts

# ------------------ شخصية البوت (عدّلها زي ما تبي) ------------------
BOT_PERSONALITY = os.getenv(
    "BOT_PERSONALITY",
    "أنت شخصية كرتونية مرحة ومحبوبة، تتكلم بأسلوب خفيف ظريف، "
    "ردودك قصيرة (جملة أو جملتين بالكثير) لأنك بتتكلم صوتياً مو كتابة.",
)

groq_client = Groq(api_key=GROQ_API_KEY)

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = discord.Bot(intents=intents)

# محادثة كل مستخدم نحفظها عشان البوت يفتكر السياق
conversation_history = defaultdict(list)


def get_llm_reply(user_id: int, user_text: str) -> str:
    history = conversation_history[user_id]
    history.append({"role": "user", "content": user_text})
    history = history[-10:]  # نحتفظ بآخر ١٠ رسائل بس

    messages = [{"role": "system", "content": BOT_PERSONALITY}] + history

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        max_tokens=150,
    )
    reply = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})
    conversation_history[user_id] = history
    return reply


def get_tts_audio(text: str) -> bytes | None:
    """يرسل النص لسيرفر Colab (F5-TTS) ويرجع الصوت كـ bytes (wav)."""
    if not COLAB_TTS_URL:
        return None
    try:
        resp = requests.post(COLAB_TTS_URL, json={"text": text}, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"TTS request failed: {e}")
        return None


async def play_audio_in_vc(voice_client: discord.VoiceClient, audio_bytes: bytes):
    if voice_client is None or not voice_client.is_connected():
        return

    temp_path = f"/tmp/tts_{int(time.time()*1000)}.wav"
    with open(temp_path, "wb") as f:
        f.write(audio_bytes)

    while voice_client.is_playing():
        await asyncio.sleep(0.2)

    source = discord.FFmpegPCMAudio(temp_path)
    voice_client.play(source, after=lambda e: os.remove(temp_path) if os.path.exists(temp_path) else None)


# ------------------ أوامر البوت ------------------

@bot.slash_command(name="join", description="يخلي البوت ينضم لروم صوتي")
async def join(ctx: discord.ApplicationContext):
    if ctx.author.voice is None:
        await ctx.respond("لازم تكون داخل روم صوتي أول.")
        return

    await ctx.defer()

    try:
        channel = ctx.author.voice.channel
        await channel.connect()
        await ctx.respond(f"انضميت لـ {channel.name} 🎧 اكتب /say وقولي وش أرد فيه بصوتي.")
    except Exception as e:
        print(f"❌ خطأ بأمر join: {e}")
        await ctx.respond(f"⚠️ صار خطأ وأنا أحاول أدخل: {e}")


@bot.slash_command(name="say", description="اكتب نص والبوت يرد عليه بصوته بالروم الصوتي")
async def say(ctx: discord.ApplicationContext, text: discord.Option(str, "وش تبي البوت يرد عليه؟")):
    if ctx.voice_client is None or not ctx.voice_client.is_connected():
        await ctx.respond("لازم أدخل روم صوتي أول - استخدم /join.")
        return

    await ctx.defer()

    try:
        loop = asyncio.get_event_loop()
        reply_text = await loop.run_in_executor(None, get_llm_reply, ctx.author.id, text)
        await ctx.respond(f"🤖 **الرد:** {reply_text}")

        audio_reply = await loop.run_in_executor(None, get_tts_audio, reply_text)
        if audio_reply:
            await play_audio_in_vc(ctx.voice_client, audio_reply)
        else:
            await ctx.channel.send("⚠️ ما قدرت أطلع صوت (تأكد إن Colab شغال).")
    except Exception as e:
        await ctx.channel.send(f"⚠️ صار خطأ: {e}")


@bot.slash_command(name="leave", description="يخلي البوت يطلع من الروم الصوتي")
async def leave(ctx: discord.ApplicationContext):
    await ctx.defer()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.respond("طلعت من الروم الصوتي 👋")
    else:
        await ctx.respond("أنا مو داخل أي روم أصلاً.")


@bot.event
async def on_ready():
    print(f"✅ البوت شغال باسم {bot.user}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

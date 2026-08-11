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
FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY")
FISH_AUDIO_VOICE_ID = os.getenv("FISH_AUDIO_VOICE_ID")  # اختياري: ID لصوت مستنسخ من حسابك بـ Fish Audio

# ------------------ شخصية البوت (عدّلها زي ما تبي) ------------------
BOT_PERSONALITY = os.getenv(
    "BOT_PERSONALITY",
    "You are a fun, likable cartoon-style character. You always reply in English only, "
    "no matter what language the user writes in. Keep replies short (1-2 sentences max) "
    "since you're speaking out loud, not typing. Be witty, warm, and natural.",
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
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=150,
    )
    reply = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply})
    conversation_history[user_id] = history
    return reply


def get_tts_audio(text: str) -> bytes | None:
    """يرسل النص لـ Fish Audio API ويرجع الصوت كـ bytes (mp3)."""
    if not FISH_AUDIO_API_KEY:
        print("FISH_AUDIO_API_KEY غير محدد.")
        return None
    try:
        payload = {
            "text": text,
            "format": "mp3",
            "normalize": True,
        }
        if FISH_AUDIO_VOICE_ID:
            payload["reference_id"] = FISH_AUDIO_VOICE_ID

        resp = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": f"Bearer {FISH_AUDIO_API_KEY}",
                "Content-Type": "application/json",
                "model": "s2.1-pro-free",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"Fish Audio TTS request failed: {e}")
        return None


async def play_audio_in_vc(voice_client: discord.VoiceClient, audio_bytes: bytes):
    if voice_client is None or not voice_client.is_connected():
        return

    temp_path = f"/tmp/tts_{int(time.time()*1000)}.mp3"
    with open(temp_path, "wb") as f:
        f.write(audio_bytes)

    while voice_client.is_playing():
        await asyncio.sleep(0.2)

    source = discord.FFmpegPCMAudio(temp_path)
    voice_client.play(source, after=lambda e: os.remove(temp_path) if os.path.exists(temp_path) else None)


# ------------------ أوامر البوت ------------------

@bot.slash_command(name="join", description="Make the bot join a voice channel")
async def join(ctx: discord.ApplicationContext):
    if ctx.author.voice is None:
        await ctx.respond("You need to be in a voice channel first.")
        return

    await ctx.defer()

    try:
        channel = ctx.author.voice.channel
        await channel.connect()
        await ctx.respond(f"Joined {channel.name} 🎧 Use /say to tell me what to respond to.")
    except Exception as e:
        print(f"❌ Error in join command: {e}")
        await ctx.respond(f"⚠️ An error occurred while trying to join: {e}")


@bot.slash_command(name="say", description="Type text and the bot will reply out loud in the voice channel")
async def say(ctx: discord.ApplicationContext, text: discord.Option(str, "What do you want the bot to respond to?")):
    if ctx.voice_client is None or not ctx.voice_client.is_connected():
        await ctx.respond("I need to join a voice channel first - use /join.")
        return

    await ctx.defer()

    try:
        loop = asyncio.get_event_loop()
        reply_text = await loop.run_in_executor(None, get_llm_reply, ctx.author.id, text)
        await ctx.respond(f"🤖 **Reply:** {reply_text}")

        audio_reply = await loop.run_in_executor(None, get_tts_audio, reply_text)
        if audio_reply:
            await play_audio_in_vc(ctx.voice_client, audio_reply)
        else:
            await ctx.channel.send("⚠️ Could not generate audio (check your Fish Audio key).")
    except Exception as e:
        await ctx.channel.send(f"⚠️ An error occurred: {e}")


@bot.slash_command(name="leave", description="Make the bot leave the voice channel")
async def leave(ctx: discord.ApplicationContext):
    await ctx.defer()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.respond("Left the voice channel 👋")
    else:
        await ctx.respond("I'm not in a voice channel.")


@bot.event
async def on_ready():
    print(f"✅ Bot is running as {bot.user}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

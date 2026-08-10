"""
بوت ديسكورد صوتي - يسمع في الروم الصوتي، يفهم بشخصية معينة، ويرد بصوت مستنسخ.

خط سير العمل (Pipeline):
  1. البوت ينضم للروم الصوتي ويسجل صوت كل مستخدم على حدة.
  2. يستخدم كشف الصمت (VAD) عشان يعرف متى الشخص خلص كلامه.
  3. يحول المقطع الصوتي لنص عن طريق faster-whisper (محلي، مجاني).
  4. يرسل النص لنموذج Groq (LLM مجاني وسريع) مع شخصية البوت.
  5. يرسل رد النص لسيرفر Colab (F5-TTS) عشان يرجع صوت مستنسخ.
  6. يشغل الصوت في نفس الروم الصوتي.

ملاحظة: هذا سكيلتون (هيكل أساسي) شغال، بس يحتاج ضبط (tuning) لعتبة
الصمت (SILENCE_THRESHOLD) حسب جودة المايك والضوضاء المحيطة.
"""

import asyncio
import io
import os
import time
import wave
from collections import defaultdict

import discord
import numpy as np
import requests
import webrtcvad
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from groq import Groq

load_dotenv()

# ------------------ سيرفر صغير بس عشان منصات الاستضافة (Koyeb/Render) ------------------
# بعض الخطط المجانية تشترط إن الخدمة تفتح بورت HTTP عشان تعتبرها "شغالة".
# هذا سيرفر بسيط جداً يرد بـ "OK" ويشتغل بخيط منفصل، ما له علاقة بمنطق البوت.
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
            pass  # يسكت اللوقات عشان ما تعج بطلبات الفحص

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

# ------------------ إعدادات كشف الصمت (VAD) ------------------
SAMPLE_RATE = 48000  # ديسكورد يرسل الصوت بهذا المعدل
VAD_FRAME_MS = 20
SILENCE_MS_TO_END = 900  # لو سكت هالمدة، نعتبر إنه خلص كلامه
VAD_AGGRESSIVENESS = 2  # 0-3 (كل ما زاد رقم صار أشد بكشف الصمت)

groq_client = Groq(api_key=GROQ_API_KEY)
whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = discord.Bot(intents=intents)

# محادثة كل مستخدم نحفظها عشان البوت يفتكر السياق
conversation_history = defaultdict(list)


class VoiceListener(discord.sinks.Sink):
    """
    Sink مخصص يستقبل الصوت خام (PCM) لكل مستخدم على حدة،
    ويستخدم VAD عشان يحدد متى ينتهي كل مقطع كلام ويبدأ يعالجه.
    """

    def __init__(self, voice_client, text_channel):
        super().__init__()
        self.voice_client = voice_client
        self.text_channel = text_channel
        self.buffers = defaultdict(bytearray)
        self.last_voice_time = defaultdict(lambda: time.time())
        self.processing = set()

    def write(self, data, user):
        # data: PCM صوت خام لكل حزمة (20ms تقريباً)
        if user is None or user in self.processing:
            return

        self.buffers[user].extend(data)

        # نتحقق هل فيه صوت (كلام) بهذي الحزمة أو صمت
        try:
            is_speech = vad.is_speech(data[:960], SAMPLE_RATE)  # 20ms @ 48kHz mono-ish
        except Exception:
            is_speech = True  # لو صار خطأ بالتحليل، نفترض إنه كلام عشان ما نفوّت شي

        now = time.time()
        if is_speech:
            self.last_voice_time[user] = now
        else:
            silence_duration = (now - self.last_voice_time[user]) * 1000
            buffered_ms = len(self.buffers[user]) / (SAMPLE_RATE * 2) * 1000
            if silence_duration > SILENCE_MS_TO_END and buffered_ms > 500:
                # خلص كلامه - نبعث المقطع للمعالجة
                audio_chunk = bytes(self.buffers[user])
                self.buffers[user] = bytearray()
                self.processing.add(user)
                asyncio.run_coroutine_threadsafe(
                    process_utterance(self, user, audio_chunk, self.text_channel),
                    bot.loop,
                )

    def cleanup(self):
        super().cleanup()


def pcm_to_wav_bytes(pcm_data: bytes, channels=2, sample_width=2, rate=48000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


async def process_utterance(sink: VoiceListener, user, pcm_data: bytes, text_channel):
    """يحول المقطع الصوتي لنص، يرسله للـ LLM، ويشغل رد صوتي."""
    try:
        wav_bytes = pcm_to_wav_bytes(pcm_data)

        # 1) تحويل صوت لنص (STT) - نشغلها بخيط منفصل عشان ما توقف البوت
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, transcribe_audio, wav_bytes)

        if not text or len(text.strip()) < 2:
            return  # ما فيه كلام واضح، تجاهل

        await text_channel.send(f"🎤 **{user.display_name}:** {text}")

        # 2) رد الذكاء الاصطناعي (LLM)
        reply_text = await loop.run_in_executor(None, get_llm_reply, user.id, text)
        await text_channel.send(f"🤖 **الرد:** {reply_text}")

        # 3) تحويل النص لصوت (TTS) عن طريق سيرفر Colab
        audio_reply = await loop.run_in_executor(None, get_tts_audio, reply_text)

        if audio_reply:
            await play_audio_in_vc(sink.voice_client, audio_reply)

    except Exception as e:
        await text_channel.send(f"⚠️ صار خطأ: {e}")
    finally:
        sink.processing.discard(user)


def transcribe_audio(wav_bytes: bytes) -> str:
    buf = io.BytesIO(wav_bytes)
    segments, _ = whisper_model.transcribe(buf, language="ar")
    return " ".join(seg.text for seg in segments).strip()


def get_llm_reply(user_id: int, user_text: str) -> str:
    history = conversation_history[user_id]
    history.append({"role": "user", "content": user_text})
    # نحتفظ بآخر ١٠ رسائل بس عشان ما يكبر السياق كثير
    history = history[-10:]

    messages = [{"role": "system", "content": BOT_PERSONALITY}] + history

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",  # سريع ومجاني على Groq
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

    # ننتظر لو فيه صوت شغال حالياً
    while voice_client.is_playing():
        await asyncio.sleep(0.2)

    source = discord.FFmpegPCMAudio(temp_path)
    voice_client.play(source, after=lambda e: os.remove(temp_path) if os.path.exists(temp_path) else None)


# ------------------ أوامر البوت ------------------

@bot.slash_command(name="join", description="يخلي البوت ينضم لروم صوتي ويبدأ يسمع")
async def join(ctx: discord.ApplicationContext):
    if ctx.author.voice is None:
        await ctx.respond("لازم تكون داخل روم صوتي أول.")
        return

    channel = ctx.author.voice.channel
    voice_client = await channel.connect()
    sink = VoiceListener(voice_client, ctx.channel)
    voice_client.start_recording(sink, finished_callback, ctx.channel)
    await ctx.respond(f"انضميت لـ {channel.name} وبديت أسمع 🎧")


async def finished_callback(sink, channel):
    pass  # يستدعى لما يوقف التسجيل


@bot.slash_command(name="leave", description="يخلي البوت يطلع من الروم الصوتي")
async def leave(ctx: discord.ApplicationContext):
    if ctx.voice_client:
        ctx.voice_client.stop_recording()
        await ctx.voice_client.disconnect()
        await ctx.respond("طلعت من الروم الصوتي 👋")
    else:
        await ctx.respond("أنا مو داخل أي روم أصلاً.")


@bot.event
async def on_ready():
    print(f"✅ البوت شغال باسم {bot.user}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

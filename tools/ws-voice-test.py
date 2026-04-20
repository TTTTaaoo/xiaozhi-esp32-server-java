#!/usr/bin/env python3
"""
小智 ESP32 WebSocket 语音测试脚本
模拟 ESP32 设备完整的语音交互流程：
  WebSocket 连接 → Hello 握手 → 麦克风录音 → Opus 编码 → 发送音频帧
  → 接收 STT/LLM/TTS 消息 → Opus 解码 → 扬声器播放

使用方法:
  1. 安装依赖: pip3 install websockets opuslib pyaudio
  2. macOS 安装 opus 库: brew install opus portaudio
  3. 运行: python3 tools/ws-voice-test.py
  4. 按 Enter 开始说话，再按 Enter 停止
  5. 输入 text:你好 直接发送文本（跳过 STT）
  6. 输入 quit 退出
"""

import argparse
import asyncio
import json
import struct
import sys
import threading
import time

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip3 install websockets")
    sys.exit(1)

try:
    import opuslib
except ImportError:
    print("请安装 opuslib: pip3 install opuslib")
    print("macOS 还需要: brew install opus")
    sys.exit(1)

try:
    import pyaudio
except ImportError:
    print("请安装 pyaudio: pip3 install pyaudio")
    print("macOS 还需要: brew install portaudio")
    sys.exit(1)

# 音频参数（与服务端一致）
SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_DURATION_MS = 60
FRAME_SIZE = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 960 samples
BYTES_PER_FRAME = FRAME_SIZE * 2  # 16-bit PCM = 2 bytes per sample


class XiaozhiClient:
    """模拟 ESP32 设备的 WebSocket 客户端"""

    def __init__(self, ws_url, device_id):
        self.ws_url = ws_url
        self.device_id = device_id
        self.ws = None
        self.session_id = None
        self.is_recording = False
        self.is_running = True
        self.opus_encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_AUDIO)
        self.opus_decoder = opuslib.Decoder(SAMPLE_RATE, CHANNELS)
        self.tts_audio_buffer = []
        self.tts_playing = False
        self.send_frame_count = 0
        self.recv_frame_count = 0
        self.pa = pyaudio.PyAudio()

    def log(self, tag, msg):
        timestamp = time.strftime("%H:%M:%S")
        colors = {
            "INFO": "\033[90m",
            "SEND": "\033[36m",
            "RECV": "\033[32m",
            "AUDIO": "\033[35m",
            "ERROR": "\033[31m",
            "RESULT": "\033[33m",
        }
        color = colors.get(tag, "\033[0m")
        print(f"{color}[{timestamp}] [{tag}] {msg}\033[0m")

    async def connect(self):
        """建立 WebSocket 连接"""
        url = f"{self.ws_url}?device-id={self.device_id}"
        self.log("INFO", f"连接: {url}")

        try:
            self.ws = await websockets.connect(url, max_size=None)
            self.log("INFO", "✅ WebSocket 已连接")
            await self.send_hello()
            return True
        except Exception as e:
            self.log("ERROR", f"连接失败: {e}")
            return False

    async def send_hello(self):
        """发送 Hello 握手消息"""
        hello = {
            "type": "hello",
            "features": {"mcp": False, "aec": False},
            "audio_params": {
                "channels": CHANNELS,
                "format": "opus",
                "sample_rate": SAMPLE_RATE,
                "frame_duration": FRAME_DURATION_MS,
            },
        }
        await self.send_json(hello)

    async def send_json(self, obj):
        """发送 JSON 消息"""
        if not self.ws:
            return
        data = json.dumps(obj, ensure_ascii=False)
        await self.ws.send(data)
        self.log("SEND", data)

    async def send_binary(self, data):
        """发送二进制数据"""
        if not self.ws:
            return
        await self.ws.send(data)

    async def receive_loop(self):
        """接收消息循环"""
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    self.handle_binary(message)
                else:
                    self.handle_text(message)
        except websockets.ConnectionClosed as e:
            self.log("INFO", f"连接关闭: code={e.code} reason={e.reason}")
        except Exception as e:
            self.log("ERROR", f"接收异常: {e}")
        finally:
            self.is_running = False

    def handle_text(self, data):
        """处理文本消息"""
        self.log("RECV", data)
        try:
            msg = json.loads(data)
            msg_type = msg.get("type", "")

            if msg_type == "hello":
                self.session_id = msg.get("session_id", "")
                self.log("INFO", f"✅ 握手成功 session={self.session_id}")

            elif msg_type == "stt":
                text = msg.get("text", "")
                self.log("RESULT", f"🎤 STT: {text}")

            elif msg_type == "tts":
                state = msg.get("state", "")
                if state == "start":
                    self.tts_audio_buffer = []
                    self.tts_playing = True
                    self.recv_frame_count = 0
                    self.log("INFO", "🔊 TTS 开始")
                elif state == "sentence_start":
                    text = msg.get("text", "")
                    self.log("RESULT", f"🔊 TTS: {text}")
                elif state == "stop":
                    self.tts_playing = False
                    self.log("INFO", f"🔊 TTS 结束, 共 {self.recv_frame_count} 帧")
                    self.play_tts_audio()

            elif msg_type == "llm":
                emotion = msg.get("emotion", "")
                if emotion:
                    self.log("RESULT", f"🧠 情感: {emotion}")

            elif msg_type == "iot":
                commands = msg.get("commands", [])
                self.log("INFO", f"IoT 命令: {json.dumps(commands)}")

        except json.JSONDecodeError as e:
            self.log("ERROR", f"JSON 解析失败: {e}")

    def handle_binary(self, data):
        """处理二进制音频帧"""
        self.recv_frame_count += 1
        self.tts_audio_buffer.append(data)
        if self.recv_frame_count <= 3 or self.recv_frame_count % 10 == 0:
            self.log("AUDIO", f"收到音频帧 #{self.recv_frame_count} ({len(data)} bytes)")

    def play_tts_audio(self):
        """解码并播放 TTS 音频"""
        if not self.tts_audio_buffer:
            return

        self.log("INFO", "▶ 正在播放 TTS 音频...")

        try:
            pcm_data = bytearray()
            for opus_frame in self.tts_audio_buffer:
                try:
                    decoded = self.opus_decoder.decode(opus_frame, FRAME_SIZE)
                    pcm_data.extend(decoded)
                except Exception:
                    pass

            if not pcm_data:
                self.log("INFO", "TTS 音频解码失败，跳过播放")
                return

            duration = len(pcm_data) / (SAMPLE_RATE * 2)
            self.log("INFO", f"播放时长: {duration:.2f}s")

            stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
            )
            stream.write(bytes(pcm_data))
            stream.stop_stream()
            stream.close()
            self.log("INFO", "✅ TTS 播放完成")

        except Exception as e:
            self.log("ERROR", f"TTS 播放失败: {e}")

    async def start_recording(self):
        """开始录音并发送 Opus 编码的音频帧"""
        self.is_recording = True
        self.send_frame_count = 0

        await self.send_json({"type": "listen", "state": "start", "mode": "manual"})

        self.log("INFO", "🎤 开始录音... (按 Enter 停止)")

        stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=FRAME_SIZE,
        )

        try:
            while self.is_recording and self.is_running:
                pcm_data = stream.read(FRAME_SIZE, exception_on_overflow=False)
                opus_frame = self.opus_encoder.encode(pcm_data, FRAME_SIZE)
                await self.send_binary(opus_frame)
                self.send_frame_count += 1

                if self.send_frame_count % 15 == 0:
                    self.log("AUDIO", f"发送帧 #{self.send_frame_count} ({len(opus_frame)} bytes opus)")
        except Exception as e:
            self.log("ERROR", f"录音异常: {e}")
        finally:
            stream.stop_stream()
            stream.close()

        await self.send_json({"type": "listen", "state": "stop", "mode": "manual"})
        self.log("INFO", f"⏹ 停止录音, 共发送 {self.send_frame_count} 帧")

    async def send_text(self, text):
        """发送文本消息（跳过 STT）"""
        await self.send_json({
            "type": "listen",
            "state": "text",
            "mode": "manual",
            "text": text,
        })
        self.log("RESULT", f"📝 文本输入: {text}")

    async def abort(self):
        """发送打断指令"""
        await self.send_json({"type": "abort", "reason": "user_manual"})

    def cleanup(self):
        """清理资源"""
        self.pa.terminate()


async def main():
    parser = argparse.ArgumentParser(description="小智 ESP32 WebSocket 语音测试")
    parser.add_argument(
        "--url",
        default="ws://192.168.31.160:8092/ws/xiaozhi/v1/",
        help="WebSocket 服务地址",
    )
    parser.add_argument(
        "--device-id",
        default="test-python-001",
        help="设备 ID",
    )
    args = parser.parse_args()

    client = XiaozhiClient(args.url, args.device_id)

    if not await client.connect():
        client.cleanup()
        return

    # 启动接收循环
    recv_task = asyncio.create_task(client.receive_loop())

    print("\n" + "=" * 60)
    print("  小智 WebSocket 语音测试")
    print("=" * 60)
    print("  命令:")
    print("    Enter     - 开始/停止录音")
    print("    text:xxx  - 发送文本（跳过 STT）")
    print("    abort     - 打断当前对话")
    print("    quit      - 退出")
    print("=" * 60 + "\n")

    recording_task = None

    def input_thread():
        """在单独线程中读取用户输入"""
        loop = asyncio.get_event_loop()
        while client.is_running:
            try:
                line = input()
            except EOFError:
                break

            if line.strip().lower() == "quit":
                client.is_running = False
                break
            elif line.strip().lower() == "abort":
                asyncio.run_coroutine_threadsafe(client.abort(), loop)
            elif line.strip().lower().startswith("text:"):
                text = line.strip()[5:].strip()
                if text:
                    asyncio.run_coroutine_threadsafe(client.send_text(text), loop)
            else:
                if client.is_recording:
                    client.is_recording = False
                else:
                    asyncio.run_coroutine_threadsafe(client.start_recording(), loop)

    input_t = threading.Thread(target=input_thread, daemon=True)
    input_t.start()

    try:
        while client.is_running:
            await asyncio.sleep(0.1)
    except KeyboardInterrupt:
        client.log("INFO", "收到中断信号")
    finally:
        client.is_running = False
        client.is_recording = False
        if client.ws:
            await client.ws.close()
        recv_task.cancel()
        client.cleanup()
        client.log("INFO", "已退出")


if __name__ == "__main__":
    asyncio.run(main())

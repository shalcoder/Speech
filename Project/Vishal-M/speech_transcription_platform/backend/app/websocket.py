from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException
import azure.cognitiveservices.speech as speechsdk
import asyncio
import subprocess
import os # <-- Import os
from typing import Optional

from .transcription import transcription_service
from .logger import get_logger

router = APIRouter(prefix="/ws", tags=["websocket"])
logger = get_logger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info("websocket_connected", client_id=client_id)

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info("websocket_disconnected", client_id=client_id)

manager = ConnectionManager()

@router.websocket("/recognize-continuous")
async def websocket_continuous_recognition(websocket: WebSocket):
    client_id = f"continuous_{id(websocket)}"
    await manager.connect(websocket, client_id)

    stream: Optional[speechsdk.audio.PushAudioInputStream] = None
    recognizer = None
    ffmpeg_process = None

    try:
        # Define the audio format for the Speech SDK (raw PCM)
        audio_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)

        async def send_result(result: dict):
            try:
                if client_id in manager.active_connections:
                    await manager.active_connections[client_id].send_json(result)
            except Exception as e:
                logger.error("failed_to_send_result", error=str(e))

        recognizer = await transcription_service.recognize_continuous(
            stream,
            send_result
        )

        # FFmpeg command to convert input (likely webm/opus) to raw PCM
        ffmpeg_command = [
            "ffmpeg",
            "-i", "pipe:0",        # Input from stdin
            "-f", "s16le",         # Output format: signed 16-bit little-endian PCM
            "-acodec", "pcm_s16le", # Audio codec
            "-ar", "16000",        # Sample rate
            "-ac", "1",            # Mono channel
            "pipe:1"               # Output to stdout
        ]

        # --- FIX FOR RENDER DEPLOYMENT ---
        # `CREATE_NO_WINDOW` is a Windows-only flag. Check OS.
        creation_flags = 0
        if os.name == 'nt':  # 'nt' is the name for Windows
            creation_flags = subprocess.CREATE_NO_WINDOW
        # --- END OF FIX ---

        ffmpeg_process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creation_flags  # Apply platform-safe flag
        )

        # Task to read FFmpeg's stdout (raw audio) and push to Azure
        async def read_ffmpeg_stdout():
            while True:
                if ffmpeg_process and ffmpeg_process.stdout:
                    data = await ffmpeg_process.stdout.read(1024)
                    if not data: break
                    stream.write(data)
                else: break
            logger.info("FFmpeg stdout read task finished.", client_id=client_id)

        # Task to read FFmpeg's stderr (for debugging)
        async def read_ffmpeg_stderr():
            while True:
                if ffmpeg_process and ffmpeg_process.stderr:
                    line = await ffmpeg_process.stderr.readline()
                    if not line: break
                    logger.info(f"ffmpeg_stderr: {line.decode().strip()}", client_id=client_id)
                else: break
            logger.info("FFmpeg stderr read task finished.", client_id=client_id)

        stdout_task = asyncio.create_task(read_ffmpeg_stdout())
        stderr_task = asyncio.create_task(read_ffmpeg_stderr())

        # Main loop: Read from WebSocket (client) -> write to FFmpeg stdin
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_bytes(), timeout=30.0)
                if data:
                    if ffmpeg_process.stdin:
                        ffmpeg_process.stdin.write(data)
                        await ffmpeg_process.stdin.drain()
                    else:
                        logger.warning("FFmpeg stdin not available.", client_id=client_id)
                        break
            except asyncio.TimeoutError:
                logger.warning("websocket_receive_timeout", client_id=client_id)
                break
            except WebSocketDisconnect:
                logger.info("websocket_client_disconnected", client_id=client_id)
                break

        # Close FFmpeg's stdin after WebSocket closes
        if ffmpeg_process.stdin:
            try:
                ffmpeg_process.stdin.close()
                await ffmpeg_process.stdin.wait_closed()
            except Exception as e:
                logger.warning(f"Error closing ffmpeg stdin: {e}", client_id=client_id)

        # Wait for tasks to complete
        await stdout_task
        await stderr_task
        if ffmpeg_process:
            await ffmpeg_process.wait()
            logger.info("FFmpeg process finished.", client_id=client_id)

    except Exception as e:
        logger.error("continuous_recognition_error", error=str(e), exc_info=True, client_id=client_id)
        try:
            await websocket.send_json({"status": "error", "error": str(e)})
        except: pass # Client might already be disconnected
    finally:
        # --- Cleanup ---
        if recognizer:
            try:
                await asyncio.get_event_loop().run_in_executor(None, recognizer.stop_continuous_recognition)
                logger.info("Azure recognizer stopped", client_id=client_id)
            except Exception as e:
                logger.error("Error stopping recognizer", error=str(e), client_id=client_id)
        if stream:
            stream.close()
            logger.info("PushAudioInputStream closed", client_id=client_id)
        if ffmpeg_process and ffmpeg_process.returncode is None:
            try:
                ffmpeg_process.kill()
                await ffmpeg_process.wait()
                logger.info("FFmpeg process killed", client_id=client_id)
            except Exception as e:
                logger.error("Error killing ffmpeg", error=str(e), client_id=client_id)
        manager.disconnect(client_id)
        try:
            await websocket.close()
        except: pass

@router.websocket("/recognize-once")
async def websocket_single_recognition(websocket: WebSocket):
    client_id = f"once_{id(websocket)}"
    await manager.connect(websocket, client_id)

    stream: Optional[speechsdk.audio.PushAudioInputStream] = None
    ffmpeg_process = None

    try:
        # Define audio format for raw PCM
        audio_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)

        timeout_duration = 10.0
        start_time = asyncio.get_event_loop().time()

        # --- FFmpeg setup ---
        ffmpeg_command = ["ffmpeg", "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "pipe:1"]
        
        # --- FIX FOR RENDER DEPLOYMENT ---
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.CREATE_NO_WINDOW
            
        ffmpeg_process = await asyncio.create_subprocess_exec(
            *ffmpeg_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creation_flags
        )

        async def read_stdout_once():
            while True:
                if ffmpeg_process and ffmpeg_process.stdout:
                    data = await ffmpeg_process.stdout.read(1024)
                    if not data: break
                    stream.write(data)
                else: break
            logger.info("FFmpeg_once stdout read task finished.", client_id=client_id)
        
        async def read_stderr_once():
            while True:
                if ffmpeg_process and ffmpeg_process.stderr:
                    line = await ffmpeg_process.stderr.readline()
                    if not line: break
                    logger.info(f"ffmpeg_once_stderr: {line.decode().strip()}", client_id=client_id)
                else: break
            logger.info("FFmpeg_once stderr read task finished.", client_id=client_id)
        
        stdout_task_once = asyncio.create_task(read_stdout_once())
        stderr_task_once = asyncio.create_task(read_stderr_once())

        # Read from WebSocket
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_duration:
                logger.warning("Recognize-once timeout reached.", client_id=client_id)
                break
            try:
                remaining = timeout_duration - elapsed
                data = await asyncio.wait_for(websocket.receive_bytes(), timeout=min(remaining, 1.0))
                if ffmpeg_process.stdin:
                    ffmpeg_process.stdin.write(data)
                    await ffmpeg_process.stdin.drain()
                else: break
            except asyncio.TimeoutError: break
            except WebSocketDisconnect: break
        
        # Close FFmpeg input
        if ffmpeg_process.stdin:
            try:
                ffmpeg_process.stdin.close()
                await ffmpeg_process.stdin.wait_closed()
            except: pass
        
        # Wait for FFmpeg
        await stdout_task_once
        await stderr_task_once
        if ffmpeg_process:
            await ffmpeg_process.wait()

        # Close Azure stream *after* FFmpeg is done
        stream.close()

        # Get result and send
        result = await transcription_service.recognize_from_stream(stream)
        await websocket.send_json(result)

    except Exception as e:
        logger.error("single_recognition_error", error=str(e), exc_info=True, client_id=client_id)
        try:
            await websocket.send_json({"status": "error", "error": str(e)})
        except: pass
    finally:
        # --- Cleanup ---
        if stream:
            try: stream.close()
            except: pass
        if ffmpeg_process and ffmpeg_process.returncode is None:
            try:
                ffmpeg_process.kill()
                await ffmpeg_process.wait()
            except Exception as e:
                logger.error("Error killing ffmpeg_once", error=str(e), client_id=client_id)
        manager.disconnect(client_id)
        try:
            await websocket.close()
        except: pass
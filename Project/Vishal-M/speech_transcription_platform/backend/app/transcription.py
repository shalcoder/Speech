import azure.cognitiveservices.speech as speechsdk
import asyncio
from typing import Dict, Optional, List
from .config import get_settings
from .logger import get_logger
import time

settings = get_settings()
logger = get_logger(__name__)

class TranscriptionService:
    def __init__(self):
        self.speech_key = settings.SPEECH_KEY
        self.service_region = settings.SERVICE_REGION
        self.auto_detect_config = speechsdk.AutoDetectSourceLanguageConfig(
            languages=["en-US", "hi-IN", "es-ES", "fr-FR", "de-DE"]
        )

    def _get_speech_config(self) -> speechsdk.SpeechConfig:
        config = speechsdk.SpeechConfig(
            subscription=self.speech_key,
            region=self.service_region
        )
        return config

    async def transcribe_file(self, file_path: str) -> Dict[str, Optional[str]]:
        """Transcribes a potentially long audio file using continuous recognition."""
        logger.info("transcription_started (continuous)", file_path=file_path)
        speech_config = self._get_speech_config()
        audio_config = speechsdk.AudioConfig(filename=file_path)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            auto_detect_source_language_config=self.auto_detect_config,
            audio_config=audio_config
        )

        all_results: List[str] = []
        detected_language: Optional[str] = None
        done = asyncio.Event()
        error_message: Optional[str] = None
        loop = asyncio.get_event_loop() # Get loop for threadsafe calls

        def recognized_handler(evt):
            nonlocal detected_language
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                if not detected_language: # Capture language from first result
                    try:
                        auto_detect_result = speechsdk.AutoDetectSourceLanguageResult(evt.result)
                        detected_language = auto_detect_result.language
                        logger.info(f"Detected language: {detected_language}", file_path=file_path)
                    except Exception as lang_err:
                        logger.warning("Could not extract language", error=str(lang_err))
                        detected_language = "unknown"
                all_results.append(evt.result.text)
                logger.debug(f"Recognized chunk: {evt.result.text}", file_path=file_path)

        def canceled_handler(evt):
            nonlocal error_message
            cancellation = evt.cancellation_details
            error_msg = f"Recognition canceled: {cancellation.reason}"
            if cancellation.reason == speechsdk.CancellationReason.Error and cancellation.error_details:
                error_msg += f" - Details: {cancellation.error_details}"
            logger.error(f"Transcription failed for {file_path}: {error_msg}")
            error_message = error_msg
            if not done.is_set(): loop.call_soon_threadsafe(done.set) # Signal completion

        def session_stopped_handler(evt):
            logger.info(f"Session stopped for {file_path}. Reason: {evt.session_id}")
            if not done.is_set(): loop.call_soon_threadsafe(done.set) # Signal completion

        recognizer.recognized.connect(recognized_handler)
        recognizer.canceled.connect(canceled_handler)
        recognizer.session_stopped.connect(session_stopped_handler)
        recognizer.session_started.connect(lambda evt: logger.info(f"Session started for {file_path}", session_id=evt.session_id))

        try:
            await loop.run_in_executor(None, recognizer.start_continuous_recognition)
            await done.wait() # Wait for session_stopped or canceled
            await loop.run_in_executor(None, recognizer.stop_continuous_recognition)
            logger.info(f"Continuous recognition stopped explicitly for {file_path}")
        except Exception as e:
            logger.exception(f"Exception during continuous file transcription for {file_path}: {e}")
            error_message = f"Runtime error during transcription: {str(e)}"
            try: await loop.run_in_executor(None, recognizer.stop_continuous_recognition)
            except: pass

        # Process results
        if error_message:
            return {"language": detected_language or "unknown", "text": " ".join(all_results) if all_results else None, "status": "failed", "error": error_message}
        elif not all_results:
            logger.warning("no_speech_detected (continuous)", file_path=file_path)
            return {"language": detected_language or "unknown", "text": None, "status": "no_speech", "error": "No speech detected in audio"}
        else:
            final_text = " ".join(all_results)
            logger.info("transcription_completed (continuous)", language=detected_language, text_length=len(final_text), file_path=file_path)
            return {"language": detected_language or "unknown", "text": final_text, "status": "completed", "error": None}

    async def recognize_from_stream(self, stream: speechsdk.audio.PushAudioInputStream) -> Dict[str, Optional[str]]:
        """ Performs a single recognition from an incoming audio stream. """
        try:
            speech_config = self._get_speech_config()
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, auto_detect_source_language_config=self.auto_detect_config, audio_config=audio_config)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, recognizer.recognize_once)

            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                auto_detect_result = speechsdk.AutoDetectSourceLanguageResult(result)
                return {"language": auto_detect_result.language, "text": result.text, "status": "completed", "error": None}
            elif result.reason == speechsdk.ResultReason.NoMatch:
                logger.warning("Stream (once) recognition: NoMatch")
                return {"language": None, "text": None, "status": "no_match", "error": "No speech recognized from stream."}
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation = result.cancellation_details
                error_msg = f"Stream (once) recognition canceled: {cancellation.reason}"
                if cancellation.reason == speechsdk.CancellationReason.Error and cancellation.error_details: error_msg += f" - Details: {cancellation.error_details}"
                logger.error(error_msg)
                return {"language": None, "text": None, "status": "failed", "error": error_msg}
            else:
                 logger.error(f"Stream (once) recognition ended with unknown reason: {result.reason}")
                 return {"language": None, "text": None, "status": "failed", "error": f"Unknown recognition reason: {result.reason}"}
        except Exception as e:
            logger.error("stream_recognition_failed (once)", error=str(e), exc_info=True)
            return {"language": None, "text": None, "status": "failed", "error": str(e)}

    async def recognize_continuous(self, stream: speechsdk.audio.PushAudioInputStream, callback) -> speechsdk.SpeechRecognizer:
        """ Sets up continuous recognition for a WebSocket stream. """
        recognizer = None
        try:
            speech_config = self._get_speech_config()
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, auto_detect_source_language_config=self.auto_detect_config, audio_config=audio_config)
            loop = asyncio.get_event_loop()

            def recognized_handler(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    auto_detect_result = speechsdk.AutoDetectSourceLanguageResult(evt.result)
                    asyncio.run_coroutine_threadsafe(callback({"language": auto_detect_result.language, "text": evt.result.text, "status": "recognized", "error": None}), loop)
                elif evt.result.reason == speechsdk.ResultReason.NoMatch: logger.debug("Continuous recognition (stream): NoMatch")

            def canceled_handler(evt):
                error_msg = f"Continuous recognition (stream) canceled: {evt.reason}"
                if evt.reason == speechsdk.CancellationReason.Error and evt.error_details: error_msg += f" - Details: {evt.error_details}"
                logger.warning(error_msg)
                asyncio.run_coroutine_threadsafe(callback({"status": "error", "error": error_msg, "language": None, "text": None}), loop)

            def session_stopped_handler(evt):
                logger.info("Continuous recognition session stopped (stream).")
                asyncio.run_coroutine_threadsafe(callback({"status": "stopped", "error": None, "language": None, "text": None}), loop)

            recognizer.recognized.connect(recognized_handler)
            recognizer.canceled.connect(canceled_handler)
            recognizer.session_stopped.connect(session_stopped_handler)
            recognizer.session_started.connect(lambda evt: logger.info("Session started (stream)", session_id=evt.session_id))
            
            await loop.run_in_executor(None, recognizer.start_continuous_recognition)
            logger.info("Continuous recognition started (stream).")
            return recognizer
        except Exception as e:
            logger.error("continuous_recognition_setup_failed (stream)", error=str(e), exc_info=True)
            if 'callback' in locals() and asyncio.iscoroutinefunction(callback): await callback({"status": "error", "error": f"Setup failed: {str(e)}", "language": None, "text": None})
            raise

transcription_service = TranscriptionService()


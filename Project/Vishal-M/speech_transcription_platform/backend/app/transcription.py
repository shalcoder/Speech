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
        # Supported languages for auto-detection, loaded from settings
        supported_languages = settings.SUPPORTED_LANGUAGES.split(',')
        self.auto_detect_config = speechsdk.AutoDetectSourceLanguageConfig(
            languages=supported_languages
        )
    
    def _get_speech_config(self) -> speechsdk.SpeechConfig:
        config = speechsdk.SpeechConfig(
            subscription=self.speech_key,
            region=self.service_region
        )
        # Enable detailed output format for more info if needed later
        # config.output_format = speechsdk.OutputFormat.DetailedSpeech
        return config
    
    async def transcribe_file(self, file_path: str) -> Dict[str, Optional[str]]:
        """
        Transcribes a potentially long audio file using continuous recognition.
        Accumulates results until the end of the audio stream.
        """
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

        def recognized_handler(evt):
            nonlocal detected_language
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                if not detected_language: # Capture language from the first result
                    try:
                         auto_detect_result = speechsdk.AutoDetectSourceLanguageResult(evt.result)
                         detected_language = auto_detect_result.language
                    except Exception as lang_err:
                         logger.warning("Could not extract language from first result", error=str(lang_err))
                         detected_language = "unknown" # Fallback

                all_results.append(evt.result.text)
                logger.debug(f"Recognized chunk: {evt.result.text}", file_path=file_path)

        def canceled_handler(evt):
            nonlocal error_message
            cancellation = evt.cancellation_details
            error_msg = f"Recognition canceled: {cancellation.reason}"
            if cancellation.reason == speechsdk.CancellationReason.Error:
                error_msg += f" - Details: {cancellation.error_details}"
            logger.error(f"Transcription failed for {file_path}: {error_msg}")
            error_message = error_msg
            done.set() # Signal completion on error

        def session_stopped_handler(evt):
            logger.info(f"Session stopped for {file_path}. Reason: {evt}")
            done.set() # Signal completion when the audio stream ends

        # Connect handlers
        recognizer.recognized.connect(recognized_handler)
        recognizer.canceled.connect(canceled_handler)
        recognizer.session_stopped.connect(session_stopped_handler)
        recognizer.session_started.connect(lambda evt: logger.info(f"Session started for {file_path}", event=str(evt)))
        
        loop = asyncio.get_event_loop()
        try:
            # Start continuous recognition (non-blocking)
            await loop.run_in_executor(None, recognizer.start_continuous_recognition)

            # Wait until the session stops (or cancellation occurs)
            await done.wait()

        except Exception as e:
            logger.exception(f"Exception during continuous file transcription for {file_path}: {e}")
            error_message = f"Runtime error during transcription: {str(e)}"
        finally:
            # Ensure stop is attempted even on exception
            try:
                await loop.run_in_executor(None, recognizer.stop_continuous_recognition)
                logger.info(f"Continuous recognition stopped for {file_path}")
            except Exception as stop_err:
                logger.error(f"Error stopping recognizer for {file_path}", error=str(stop_err))

        # --- Process results ---
        if error_message:
            return {
                "language": detected_language or "unknown",
                "text": " ".join(all_results) if all_results else None, # Return partial text if any
                "status": "failed",
                "error": error_message
            }
        elif not all_results:
             logger.warning("no_speech_detected (continuous)", file_path=file_path)
             return {
                 "language": detected_language or "unknown", # Language might be detected even with no speech
                 "text": None,
                 "status": "no_speech",
                 "error": "No speech detected in audio"
             }
        else:
            final_text = " ".join(all_results)
            logger.info(
                "transcription_completed (continuous)",
                language=detected_language,
                text_length=len(final_text),
                file_path=file_path
            )
            return {
                "language": detected_language or "unknown",
                "text": final_text,
                "status": "completed",
                "error": None
            }

    async def recognize_from_stream(
        self,
        stream: speechsdk.audio.PushAudioInputStream
    ) -> Dict[str, Optional[str]]:
        """ Performs a single recognition from an incoming audio stream. """
        # This function remains unchanged (uses recognize_once)
        try:
            speech_config = self._get_speech_config()
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                auto_detect_source_language_config=self.auto_detect_config,
                audio_config=audio_config
            )
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, recognizer.recognize_once)
            
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                auto_detect_result = speechsdk.AutoDetectSourceLanguageResult(result)
                return {
                    "language": auto_detect_result.language,
                    "text": result.text,
                    "status": "completed",
                    "error": None
                }
            else:
                 error_reason = result.cancellation_details.reason if result.reason == speechsdk.ResultReason.Canceled else result.reason
                 logger.warning(f"Stream (once) recognition ended: {error_reason}")
                 return {
                    "language": None, "text": None,
                    "status": "no_match_or_error", "error": f"Stream (once) ended: {error_reason}"
                 }
            
        except Exception as e:
            logger.error("stream_recognition_failed (once)", error=str(e), exc_info=True)
            return {
                "language": None, "text": None,
                "status": "failed", "error": str(e)
            }
    
    async def recognize_continuous(
        self,
        stream: speechsdk.audio.PushAudioInputStream,
        callback # This callback must be an async function
    ) -> speechsdk.SpeechRecognizer:
        """ Sets up continuous recognition for a WebSocket stream. """
        # This function remains largely unchanged
        recognizer = None # Define recognizer in the broader scope
        try:
            speech_config = self._get_speech_config()
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                auto_detect_source_language_config=self.auto_detect_config,
                audio_config=audio_config
            )
            
            loop = asyncio.get_event_loop()
            
            def recognized_handler(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    auto_detect_result = speechsdk.AutoDetectSourceLanguageResult(evt.result)
                    # Schedule the async callback to run in the event loop
                    asyncio.run_coroutine_threadsafe(
                        callback({
                            "language": auto_detect_result.language,
                            "text": evt.result.text,
                            "status": "recognized",
                            "error": None
                        }),
                        loop
                    )
                elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                     logger.debug("Continuous recognition (stream): NoMatch")

            def canceled_handler(evt):
                error_msg = f"Continuous recognition (stream) canceled: {evt.reason}"
                if evt.reason == speechsdk.CancellationReason.Error:
                     error_msg += f" - Details: {evt.error_details}"
                logger.warning(error_msg)
                asyncio.run_coroutine_threadsafe(
                     callback({"status": "error", "error": error_msg}),
                     loop
                )

            def session_stopped_handler(evt):
                logger.info("Continuous recognition session stopped (stream).")
                asyncio.run_coroutine_threadsafe(
                    callback({"status": "stopped", "error": None}),
                    loop
                )

            # Connect handlers
            recognizer.recognized.connect(recognized_handler)
            recognizer.canceled.connect(canceled_handler)
            recognizer.session_stopped.connect(session_stopped_handler)
            recognizer.session_started.connect(lambda evt: logger.info("Session started (stream)", event=str(evt)))
            
            # Start recognition (non-blocking)
            await loop.run_in_executor(None, recognizer.start_continuous_recognition)
            logger.info("Continuous recognition started (stream).")

            return recognizer # Return the recognizer so it can be stopped later
            
        except Exception as e:
            logger.error("continuous_recognition_setup_failed (stream)", error=str(e), exc_info=True)
            # Ensure callback is called with error if setup fails
            await callback({"status": "error", "error": f"Setup failed: {str(e)}"})
            raise # Re-raise the exception

# Single instance
transcription_service = TranscriptionService()
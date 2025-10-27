import { useState, useRef, useEffect } from 'react'
import { Mic, Square, Loader } from 'lucide-react'
import AudioVisualizer from '../components/AudioVisualizer'
import { BASE_URL } from '../utils/api'; // Import the centralized URL


/**
 * Converts an http/https URL to a ws/wss URL for WebSockets.
 * @param {string} url The http(s) base URL of the API
 * @returns {string} The ws(s) URL
 */
function getWebSocketBaseURL(url) {
  if (!url) return '';
  // If it's a relative path like '/api', we need to construct the full ws URL from the window location
  if (url.startsWith('/')) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${url}`;
  }
  // Otherwise, just replace http with ws
  return url.replace(/^http/, 'ws');
}

const WS_URL = getWebSocketBaseURL(BASE_URL).replace(/\/api$/, '') + '/ws/recognize-continuous';

console.log(`WebSocket URL determined as: ${WS_URL}`); // For debugging

export default function LiveTranscription() {
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false) // For initial setup state
  const [transcript, setTranscript] = useState([]) // Array to store transcript segments
  const [currentLanguage, setCurrentLanguage] = useState(null)
  const [error, setError] = useState(null)

  const wsRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const streamRef = useRef(null)
  const audioContextRef = useRef(null) // For AudioVisualizer

  const startRecording = async () => {
    console.log("Attempting to start recording..."); // Debug log
    try {
      setError(null)
      setTranscript([])
      setCurrentLanguage(null)
      setIsProcessing(true)

      console.log("Requesting microphone access..."); // Debug log
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000 // Match sample rate expected by backend/Azure
        }
      })
      streamRef.current = stream
      console.log("Microphone access granted."); // Debug log

      // Initialize AudioContext for visualizer here if needed, or pass stream
      if (!audioContextRef.current) {
          audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
      }

      console.log(`Connecting to WebSocket at ${WS_URL}`); // Debug log
      wsRef.current = new WebSocket(WS_URL)

      wsRef.current.onopen = () => {
        console.log("WebSocket connection opened."); // Debug log
        setIsRecording(true)
        setIsProcessing(false) // Finished initializing

        // Start MediaRecorder *after* WebSocket is open
        try {
          console.log("Creating MediaRecorder..."); // Debug log
          // Use a common, efficient codec like Opus in WebM container
          const options = { mimeType: 'audio/webm;codecs=opus' };
          if (!MediaRecorder.isTypeSupported(options.mimeType)) {
             console.warn("WebM/Opus not supported, trying default.");
             delete options.mimeType; // Fallback to browser default
          }
          const mediaRecorder = new MediaRecorder(stream, options);
          mediaRecorderRef.current = mediaRecorder

          mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
              // console.log(`Sending audio chunk: ${event.data.size} bytes`); // Verbose Debug
              wsRef.current.send(event.data)
            }
          }

          // Start recording, sending data frequently for lower latency
          mediaRecorder.start(250); // Send data approx 4 times per second
          console.log("MediaRecorder started."); // Debug log
        } catch (recorderError) {
           console.error("Error creating or starting MediaRecorder:", recorderError);
           setError(`MediaRecorder error: ${recorderError.message}`);
           stopRecording(); // Stop everything if recorder fails
        }
      }

      wsRef.current.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data)
            // console.log("Received WebSocket message:", data); // Debug log
            if (data.text) {
              setTranscript(prev => [...prev, {
                text: data.text,
                language: data.language,
                timestamp: new Date().toISOString() // Add timestamp for rendering key/info
              }])
              setCurrentLanguage(data.language)
            }
            if (data.status === 'error') {
              console.error("Received error from backend:", data.error); // Log backend errors
              setError(`Backend Error: ${data.error}`)
              // Optionally stop recording on certain errors
              // stopRecording();
            }
        } catch (parseError) {
             console.error("Failed to parse WebSocket message:", event.data, parseError);
             setError("Received unparseable message from backend.");
        }
      }

      wsRef.current.onerror = (wsError) => {
        console.error('WebSocket connection error:', wsError)
        setError('WebSocket connection error. Please try again.')
        stopRecording() // Clean up on error
      }

      wsRef.current.onclose = (event) => {
        console.log(`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`); // Debug log
        setIsRecording(false)
        setIsProcessing(false) // Ensure processing state is reset
        // Optionally add logic here if the close was unexpected
      }

    } catch (err) {
      console.error("Error starting recording:", err); // Log the specific error
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
         setError('Microphone access denied. Please grant permission in your browser settings.');
      } else {
         setError(`Failed to access microphone: ${err.message}`)
      }
      setIsProcessing(false)
    }
  }

  const stopRecording = () => {
    console.log("Stopping recording..."); // Debug log
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop()
      console.log("MediaRecorder stopped."); // Debug log
    }

    streamRef.current?.getTracks().forEach(track => track.stop())
    streamRef.current = null; // Clear stream ref
    console.log("Microphone stream tracks stopped."); // Debug log

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.close()
      console.log("WebSocket connection closed by client."); // Debug log
    }
    // wsRef.current should be nulled in the onclose handler

    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
       audioContextRef.current.close().catch(e => console.warn("Error closing AudioContext:", e));
       audioContextRef.current = null;
    }


    setIsRecording(false)
    setIsProcessing(false)
  }

  // Cleanup function on component unmount
  useEffect(() => {
    return () => {
      stopRecording()
    }
  }, []) // Empty dependency array ensures this runs only once on unmount

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h1 className="text-3xl font-bold text-white">Live Transcription</h1>
        <p className="mt-2 text-gray-400">
          Real-time speech recognition with continuous streaming
        </p>
      </div>

      <div className="glass-panel p-8">
        <div className="flex flex-col items-center gap-6">
          {/* Pass the stream directly if the visualizer can handle it */}
          <AudioVisualizer isActive={isRecording} stream={streamRef.current} />

          <div className="flex gap-4">
            {!isRecording ? (
              <button
                onClick={startRecording}
                disabled={isProcessing}
                className="btn-primary flex items-center gap-2 px-8 py-4 text-lg disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isProcessing ? (
                  <>
                    <Loader className="w-6 h-6 animate-spin" />
                    Initializing...
                  </>
                ) : (
                  <>
                    <Mic className="w-6 h-6" />
                    Start Recording
                  </>
                )}
              </button>
            ) : (
              <button
                onClick={stopRecording}
                className="px-8 py-4 bg-red-600 hover:bg-red-700 text-white font-medium rounded-lg transition-all shadow-lg hover:shadow-red-500/50 flex items-center gap-2 text-lg animate-pulse-glow"
              >
                <Square className="w-6 h-6" />
                Stop Recording
              </button>
            )}
          </div>

          {currentLanguage && isRecording && ( // Only show language if recording
            <div className="glass-panel px-4 py-2 border border-primary-500/30">
              <span className="text-sm text-gray-400">Detected Language:</span>
              <span className="ml-2 text-primary-400 font-semibold">{currentLanguage}</span>
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="glass-panel border-red-500/50 bg-red-900/20 p-4 animate-fade-in">
          <p className="text-red-400 font-medium">Error: <span className="font-normal">{error}</span></p>
        </div>
      )}

      <div className="glass-panel p-6">
        <h2 className="text-xl font-semibold text-white mb-4">Transcript</h2>
        <div className="space-y-3 max-h-96 overflow-y-auto pr-2">
          {transcript.length === 0 ? (
            <p className="text-gray-500 text-center py-8">
              {isRecording ? 'Listening...' : isProcessing ? 'Initializing...' : 'Start recording to see transcript'}
            </p>
          ) : (
            transcript.map((item, index) => (
              // Using index as key is okay here if list only appends and isn't reordered
              <div
                key={item.timestamp + index} // More robust key
                className="glass-panel p-4 border-l-4 border-primary-500 animate-slide-in"
              >
                <p className="text-gray-200">{item.text}</p>
                <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                  <span>{item.language}</span>
                  <span>{new Date(item.timestamp).toLocaleTimeString()}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
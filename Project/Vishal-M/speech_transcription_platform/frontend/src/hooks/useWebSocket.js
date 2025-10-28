
import { useEffect, useRef, useState, useCallback } from 'react'

// Check if we are in production
const isProduction = import.meta.env.PROD;
const API_URL = import.meta.env.VITE_API_URL; // This will be set by Render

if (isProduction && !API_URL) {
  console.error("CRITICAL: VITE_API_URL environment variable is not set for production build!");
}

/**
 * Gets the correct WebSocket URL based on the environment.
 * @param {string} wsPath The path, e.g., "/ws/recognize-continuous"
 * @returns {string | null} The full WebSocket URL or null if config missing
 */
function getWebSocketURL(wsPath) {
  if (isProduction) {
    // In production, use the VITE_API_URL
    if (!API_URL) return null; // Cannot connect
    // Replace http with ws, remove /api if present, remove trailing slash
    const wsBase = API_URL.replace(/^http/, 'ws').replace(/\/api\/?$/, '').replace(/\/$/, '');
    return `${wsBase}${wsPath}`;
  } else {
    // In development, use the current host and let Vite proxy handle it.
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host; // e.g., localhost:5173
    // Vite proxy will catch this and forward it to ws://localhost:8000
    return `${protocol}//${host}${wsPath}`;
  }
}


export function useWebSocket(urlPath, options = {}) {
  const { onOpen, onMessage, onError, onClose } = options;
  const [isConnected, setIsConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)
  const wsRef = useRef(null)
  
  // Memoize the full URL calculation
  const fullWebSocketURL = useCallback(() => getWebSocketURL(urlPath), [urlPath]);
  
  const connect = useCallback(() => {
    const url = fullWebSocketURL();
    if (!url) {
      console.error("Cannot connect WebSocket: API URL is not defined.");
      onError?.(new Error("WebSocket URL is not configured."));
      return;
    }
    
    if (wsRef.current && wsRef.current.readyState < 2) { // 0=CONNECTING, 1=OPEN
        console.warn("WebSocket connection already exists or is connecting.");
        return; 
    }

    console.log(`Attempting to connect WebSocket to: ${url}`);
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      console.log(`WebSocket connected to ${url}`);
      setIsConnected(true)
      onOpen?.()
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setLastMessage(data)
        onMessage?.(data)
      } catch (e) {
        console.error("Failed to parse WebSocket message:", event.data, e);
        onError?.(new Error("Received unparseable message from backend."));
      }
    }

    ws.onerror = (error) => {
      console.error(`WebSocket error on ${url}:`, error)
      onError?.(error)
    }

    ws.onclose = (event) => {
      console.log(`WebSocket disconnected from ${url}. Code: ${event.code}, Reason: ${event.reason}`);
      setIsConnected(false)
      wsRef.current = null // Clear ref on close
      onClose?.(event)
    }
  }, [fullWebSocketURL, onOpen, onMessage, onError, onClose]);


  const disconnect = useCallback(() => {
     if (wsRef.current) {
        console.log(`Closing WebSocket connection...`);
        // Use standard close code 1000 unless specific reason needed
        wsRef.current.close(1000, "Client initiated disconnect"); 
        // State updates (isConnected=false, wsRef=null) happen in the onclose handler
     }
  }, []);

  // Effect to disconnect on unmount
  useEffect(() => {
    // The component using the hook should call connect() explicitly.
    // This effect only handles cleanup.
    return () => {
      disconnect(); // Disconnect when component unmounts
    }
  }, [disconnect]) // Depend on memoized disconnect

  const sendMessage = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    } else {
        console.warn("WebSocket not open. Cannot send message.");
    }
  }, []);

  const sendBinary = useCallback((data) => {
     if (wsRef.current?.readyState === WebSocket.OPEN) {
       wsRef.current.send(data)
     } else {
         console.warn("WebSocket not open. Cannot send binary data.");
     }
   }, []);

  return { isConnected, lastMessage, sendMessage, sendBinary, connect, disconnect }
}
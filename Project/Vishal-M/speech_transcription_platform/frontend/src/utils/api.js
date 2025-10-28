import axios from 'axios'

// Check if we are in production (deployed via 'npm run build')
const isProduction = import.meta.env.PROD;

// Get the backend URL from environment variables set during build,
// or use the relative path '/api' which will be caught by the Vite proxy in development.
const BASE_URL = isProduction
  ? import.meta.env.VITE_API_URL // Use the URL provided during build
  : '/api'; // Vite proxy handles this in 'npm run dev'

// Log an error if the production URL is missing during a production build
if (isProduction && !BASE_URL) {
  console.error("CRITICAL: VITE_API_URL environment variable is not set during the production build!");
}

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 60000, // Increased timeout for potentially long operations like file upload
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  },
})

// Add a response interceptor for centralized error handling
api.interceptors.response.use(
  (response) => response, // Simply return successful responses
  (error) => {
    // Log the error for debugging
    console.error('API Error:', error.response || error.message || error);

    // Extract a user-friendly error message
    const message = error.response?.data?.detail || // FastAPI HTTPExceptions
                    error.message ||               // Network errors or others
                    'An unexpected error occurred.';

    // Reject the promise so component-level error handlers can catch it
    return Promise.reject({
        message: message,
        status: error.response?.status,
        data: error.response?.data
    });
  }
)

export default api;
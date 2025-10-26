# Deploying to Render (Simplified SQLite Path)

This guide explains how to deploy the application to Render using a simplified architecture with SQLite for the database.

## Services

We will create two services on Render:

1.  **Backend (Web Service)**: Runs the FastAPI application from the `backend` directory.
2.  **Frontend (Static Site)**: Serves the React application from the `frontend` directory.

## Backend Deployment (Web Service)

1.  Create a new **Web Service** on Render.
2.  Connect your Git repository.
3.  Set the following values during creation:
    *   **Root Directory**: `backend`
    *   **Environment**: `Docker`
    *   **Region**: Choose a region close to you.
    *   **Instance Type**: Choose an appropriate instance type.

4.  After the service is created, go to the **Environment** tab and add the following environment variables:

    *   `SPEECH_KEY`: Your Azure Speech API key.
    *   `SERVICE_REGION`: Your Azure Speech service region.
    *   `SECRET_KEY`: A long, random string to secure your application.
    *   `DATABASE_URL`: `sqlite:////var/data/transcription.db`
    *   `CORS_ORIGINS`: The URL of your deployed frontend (e.g., `https://your-frontend-service.onrender.com`). You can add multiple URLs separated by commas.

5.  Go to the **Disks** tab and create a new disk with the following settings:

    *   **Name**: `data`
    *   **Mount Path**: `/var/data`
    *   **Size**: Choose an appropriate size for your database.

6.  The backend will automatically deploy on every push to your connected Git repository.

## Frontend Deployment (Static Site)

1.  Create a new **Static Site** on Render.
2.  Connect the same Git repository.
3.  Set the following values during creation:
    *   **Root Directory**: `frontend`
    *   **Build Command**: `npm install && npm run build`
    *   **Publish Directory**: `frontend/dist`

4.  After the site is created, go to the **Environment** tab and add the following environment variable:

    *   `VITE_API_URL`: The URL of your deployed backend service (e.g., `https://your-backend-service.onrender.com`).

5.  Go to the **Redirects/Rewrites** tab and add the following rewrite rule:

    *   **Source**: `/*`
    *   **Destination**: `/index.html`
    *   **Action**: `Rewrite`

6.  The frontend will automatically build and deploy on every push to your connected Git repository.
# Smart Market Watchlist Frontend

React, TypeScript, and Vite interface for the Smart Market Watchlist attention engine.

## Setup

Requires a current Node.js LTS release and the FastAPI service running locally.

```powershell
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open `http://localhost:5173`.

`VITE_API_BASE_URL` is public browser configuration and defaults to `http://localhost:8000`. Set it to the deployed API URL for staging or production. Set `VITE_DEMO_MODE=false` to hide deterministic hackathon controls in a user-facing deployment.

## Validation

```powershell
npm test
npm run lint
npm run build
```

## Architecture

- `src/services` contains the centralized backend client.
- TanStack Query owns server state and request lifecycles.
- React Router owns navigation.
- `src/pages` contains watchlist, stock details, and meaningful-change views.
- `src/styles` contains design tokens and responsive application styles.
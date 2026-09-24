// Backend base URL.
//  - dev (npm run dev on :5173): talks to the FastAPI server on :8000
//  - prod (served from FastAPI's own /dist mount): same origin, so ""
export const API_BASE =
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

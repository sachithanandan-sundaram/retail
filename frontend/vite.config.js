import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets go to dist/ with relative paths so FastAPI can serve them
// from any mount point (retail_llm/server.py mounts ../frontend/dist at /).
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173 },
});

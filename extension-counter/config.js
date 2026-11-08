const CONFIG = {
  API_URL: "http://localhost:8000", // where `brainrot serve` runs
  MODE: "api", // "api" asks the Gemini model via API_URL/predict, "demo" scores from motion locally
  EARLY_FRAMES: 4, // demo mode only: quick first score
  MAX_FRAMES: 8,
  FRAME_INTERVAL_MS: 500, // chrome allows ~2 screenshots/sec
  API_TIMEOUT_MS: 60000,
  DEMO_THRESHOLD: 0.6,
  KEEP_DAYS: 7,
};

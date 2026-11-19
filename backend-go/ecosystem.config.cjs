/**
 * PM2 — Go aistock sidecar for tickflow-stock-panel
 *
 * Source of truth for aistock app settings (cwd, binary, env, logs).
 * Repo root ecosystem.config.cjs re-exports these apps so you can start from root:
 *   pm2 start ecosystem.config.cjs
 *   # or sidecar only: pm2 start ecosystem.config.cjs --only aistock
 *
 * Binary + .env live under backend-go/ (godotenv loads .env from cwd).
 *
 * Prepare:
 *   cd backend-go
 *   cp .env.example .env   # set AI_DATA_DIR, tokens, AISTOCK_SIDECAR=1, PORT=8787
 *   go build -o bin/aistock-server ./cmd/server
 *
 * Start from this directory (aistock only; does not touch FastAPI):
 *   pm2 start ecosystem.config.cjs
 *   pm2 save
 */
'use strict';

const path = require('path');
const fs = require('fs');

const root = path.resolve(__dirname);
const logsDir = path.join(root, 'logs');
const binary = path.join(root, 'bin', 'aistock-server');

if (!fs.existsSync(binary)) {
  console.error(
    '[aistock] missing bin/aistock-server — build first:\n' +
      '  cd backend-go && go build -o bin/aistock-server ./cmd/server',
  );
  process.exit(1);
}

if (!fs.existsSync(logsDir)) {
  fs.mkdirSync(logsDir, { recursive: true });
}

module.exports = {
  apps: [
    {
      name: 'aistock',
      script: binary,
      cwd: root,
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      max_restarts: 15,
      min_uptime: '10s',
      restart_delay: 3000,
      kill_timeout: 10000,
      listen_timeout: 15000,
      time: true,
      merge_logs: true,
      error_file: path.join(logsDir, 'aistock-error.log'),
      out_file: path.join(logsDir, 'aistock-out.log'),
      // Secrets and AI_DATA_DIR come from backend-go/.env via godotenv.
      // These env keys pin sidecar mode for panel proxy (AISTOCK_URL → :8787).
      env: {
        NODE_ENV: 'production',
        GIN_MODE: 'release',
        PORT: '8787',
        AISTOCK_SIDECAR: '1',
      },
    },
  ],
};

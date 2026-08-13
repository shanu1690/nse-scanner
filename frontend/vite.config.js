import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// In dev, serve the JSON bundle written by `nse-scan site` (../site/data)
// at the /data URL the app fetches in production.
function serveSiteData() {
  return {
    name: 'serve-site-data',
    configureServer(server) {
      server.middlewares.use('/data', (req, res, next) => {
        const url = decodeURIComponent(req.url.split('?')[0])
        const file = path.join(__dirname, '..', 'site', 'data', url)
        if (fs.existsSync(file) && fs.statSync(file).isFile()) {
          res.setHeader('Content-Type', 'application/json')
          fs.createReadStream(file).pipe(res)
        } else {
          next()
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), serveSiteData()],
  // Relative base so the built app works at any GitHub Pages path prefix.
  base: './',
  build: {
    outDir: '../site',
    emptyOutDir: false, // site/data is written by the Python site builder
  },
})

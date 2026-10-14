import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'

export default defineConfig({
    base: '/DataDJ/',
    build: {
        rollupOptions: {
            input: {
                explorer: fileURLToPath(new URL('./index.html', import.meta.url)),
                reduction: fileURLToPath(new URL('./reduction.html', import.meta.url)),
            },
        },
    },
})

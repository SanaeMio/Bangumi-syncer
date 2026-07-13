/**
 * 构建前同步脚本：将仓库根目录的 AGENTS.md / CLAUDE.md / CONTRIBUTING.md
 * 复制到 docs/development/_includes/ 下，供 VitePress @include 引用。
 *
 * 解决 GitHub Actions 构建时无法 @include docs/ 目录之外文件的问题。
 */
import { copyFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, '..')

const includesDir = resolve(root, 'docs/development/_includes')
const files = ['AGENTS.md', 'CLAUDE.md', 'CONTRIBUTING.md']

mkdirSync(includesDir, { recursive: true })

for (const file of files) {
  const src = resolve(root, file)
  const dest = resolve(includesDir, file)
  copyFileSync(src, dest)
  console.log(`[sync-dev-docs] ${file} -> docs/development/_includes/${file}`)
}

console.log('[sync-dev-docs] done')

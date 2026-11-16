// 前端运行时冒烟：用真实 Chromium 打开控制台，确认首页渲染、
// 切到「任务中控」后定时任务卡片可用，且没有 JS 异常。
// CI 里由 docker-smoke 工作流在容器启动后执行；本地也可先启服务再跑：
//   node web/e2e/smoke.mjs
import { chromium } from 'playwright'

const base = process.env.RADAR_BASE || 'http://127.0.0.1:8000'
const errors = []

const browser = await chromium.launch()
const page = await browser.newPage()

page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`))
page.on('console', (msg) => {
  if (msg.type() !== 'error') return
  const text = msg.text()
  // 静态资源 404（favicon 等）不算前端逻辑问题
  if (text.includes('Failed to load resource')) return
  errors.push(`console.error: ${text}`)
})

try {
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 15000 })
  await page.getByText('Weekend Radar').first().waitFor({ timeout: 10000 })

  await page.getByRole('button', { name: '任务中控' }).click()
  await page.getByText('定时任务', { exact: true }).waitFor({ timeout: 5000 })
  await page.getByRole('button', { name: '立即执行' }).waitFor({ timeout: 5000 })

  if (errors.length) {
    throw new Error(errors.join('\n'))
  }
  console.log('前端运行时冒烟通过：首页渲染 + 任务中控/定时任务可用，无 JS 异常')
} catch (e) {
  console.error('前端运行时冒烟失败:', e.message)
  process.exitCode = 1
} finally {
  await browser.close()
}

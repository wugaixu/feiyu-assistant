// Derived from dsh-wechat-chat (MIT). Independent transport, no agent runtime.
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync, copyFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { createHash, createHmac, randomBytes, randomUUID, scryptSync, timingSafeEqual } from 'node:crypto'
import { createServer, request as httpRequest } from 'node:http'
import { spawn as spawnProcess } from 'node:child_process'
import { homedir, networkInterfaces } from 'node:os'
import { fileURLToPath } from 'node:url'
import { inspectPcmWav } from './wav.mjs'
import { LocalSpeech } from './speech.mjs'
import {probePublicIngress} from './public-health.mjs'
import { VoiceStore, voiceMessageId } from './voice.js'
import { createAssistantBridge } from '../whale_assistant_bridge.mjs'
import { pathToFileURL } from 'node:url'



const DEFAULT_NICKNAME = '鲸聊助手'
const DEFAULT_TITLE = '鲸聊 · 手机'
const COOKIE_NAME = 'whale_pair'
const TUNNEL_AUTH_COOKIE = 'whale_tunnel_auth'
const DEVICE_HEADER = 'x-whale-device'
const DEFAULT_TOKEN_TTL_MS = 10 * 60 * 1000
const DEFAULT_IDLE_EXPIRE_MS = 30 * 24 * 60 * 60 * 1000
const DEFAULT_OFFLINE_AFTER_MS = 25 * 1000
const DEFAULT_MAX_DEVICES = 4
const COOKIE_MAX_AGE_SEC = 365 * 24 * 60 * 60
const MAX_BODY = 64 * 1024
// 已配对令牌的重放窗口：覆盖浏览器/WebView 对同一链接的重复导航。
const REPLAY_WINDOW_MS = 15 * 60 * 1000

const home = process.env.WHALE_HOME || '__RUNTIME_ROOT__/whale-data'
const DEVICES_FILE = join(home, 'whale-devices.json')

const AVATAR_DIR = join(home, 'wechat-chat', 'avatars')


const PAGE = readFileSync(fileURLToPath(new URL('./chat-page.html', import.meta.url)), 'utf8')
const PANEL = readFileSync(fileURLToPath(new URL('./panel-page.html', import.meta.url)), 'utf8')
const QRCODE_LIB = readFileSync(fileURLToPath(new URL('./qrcode.js', import.meta.url)), 'utf8')

/* ── ported: loopback / lan / fences ──────────────────────────────────── */

function isIPv4Loopback(v4) {
  const parts = v4.split('.')
  return parts.length === 4 && parts[0] === '127'
    && parts.every(part => /^\d{1,3}$/.test(part) && Number(part) <= 255)
}

function isLoopbackAddress(address) {
  if (address === undefined) return false
  const normalized = address.toLowerCase()
  if (normalized === '::1') return true
  if (normalized.startsWith('::ffff:')) return isIPv4Loopback(normalized.slice('::ffff:'.length))
  return isIPv4Loopback(normalized)
}

function isLoopbackHostname(hostname) {
  if (hostname === 'localhost' || hostname === '[::1]') return true
  return isIPv4Loopback(hostname)
}

function isLoopbackRequest(request) {
  if (!isLoopbackAddress(request.socket.remoteAddress)) return false
  const host = request.headers.host
  if (typeof host !== 'string') return false
  let hostUrl
  try { hostUrl = new URL('http://' + host) } catch { return false }
  if (!isLoopbackHostname(hostUrl.hostname)) return false
  if (request.headers['sec-fetch-site'] === 'cross-site') return false
  const origin = request.headers.origin
  if (origin === undefined) return true
  try { return new URL(origin).host === hostUrl.host } catch { return false }
}

/**
 * 该地址手机能否直连：排除自动私有地址（169.254/16，未拿到 DHCP）与
 * Clash/Mihomo 等代理 TUN 使用的 198.18.0.0/15 伪地址——这些在手机上不可达。
 */
function isUsableLanAddress(address) {
  if (address.startsWith('169.254.')) return false
  const [a, b] = address.split('.').map(Number)
  if (a === 198 && (b === 18 || b === 19)) return false
  return true
}

function isProxyTunnelInterface(name) {
  return /^(mihomo|clash|sing-?box|tun|wintun|tap)/i.test(String(name || '').trim())
}

function lanIPv4Addresses() {
  return Object.values(networkInterfaces()).flat()
    .filter(iface => iface !== undefined && iface.family === 'IPv4' && !iface.internal)
    .filter(iface => !isProxyTunnelInterface(iface.name) && isUsableLanAddress(iface.address))
    .map(iface => iface.address)
}

/**
 * Browser-trust fence ported from remote-web-ui routes.ts: the Host must be
 * ours (loopback or a trusted literal/public host) and browser markers same-origin.
 */
function isTrustedApiRequest(request, trustedHosts) {
  const host = request.headers.host
  if (typeof host !== 'string') return false
  let hostUrl
  try { hostUrl = new URL(`http://${host}`) } catch { return false }
  const hostname = hostUrl.hostname
  const trusted = isLoopbackRequest(request) || trustedHosts.some(entry => {
    const entryUrl = new URL(`http://${entry}`)
    return entryUrl.port === '' ? entryUrl.hostname === hostname : entryUrl.host === hostUrl.host
  })
  if (!trusted) return false
  if (request.headers['sec-fetch-site'] === 'cross-site') return false
  const origin = request.headers.origin
  if (origin === undefined) return true
  try { return new URL(origin).host === hostUrl.host } catch { return false }
}

function publicHostOf(url) {
  if (typeof url !== 'string' || url === '') return undefined
  try { return new URL(url).host } catch { return undefined }
}

/* ── helpers ──────────────────────────────────────────────────────────── */

function readCookie(header, name) {
  if (typeof header !== 'string') return undefined
  for (const part of header.split(';')) {
    const eq = part.indexOf('=')
    if (eq < 0) continue
    if (part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim()
  }
  return undefined
}

function writeJson(res, status, value, extraHeaders) {
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    ...(extraHeaders || {}),
  })
  res.end(JSON.stringify(value))
}

function readJsonBody(req, maxBytes) {
  return new Promise((resolve) => {
    const chunks = []
    let size = 0
    let done = false
    const finish = () => {
      if (done) return
      done = true
      try {
        const raw = Buffer.concat(chunks).toString('utf8')
        if (raw.trim() === '') return resolve({})
        resolve(JSON.parse(raw))
      } catch { resolve(null) }
    }
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > maxBytes) { req.removeAllListeners('data'); resolve(null); return }
      chunks.push(chunk)
    })
    req.on('end', finish)
    req.on('error', () => resolve(null))
  })
}

function readRawBody(req, maxBytes) {
  return new Promise((resolve) => {
    const chunks = []
    let size = 0
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    req.on('data', (chunk) => {
      if (settled) return
      size += chunk.length
      if (size > maxBytes) {
        finish({ ok: false, tooLarge: true })
        req.removeAllListeners('data')
        req.resume()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => finish({ ok: true, buffer: Buffer.concat(chunks) }))
    req.on('error', () => finish({ ok: false, error: true }))
    req.setTimeout?.(30_000, () => finish({ ok: false, timeout: true }))
  })
}

export class PairingService {
  constructor(config) {
    this.config = config
    this.tokens = new Map()
    this.devices = new Map()
    // 已成功消费的令牌 → 设备，短时间内允许重放同一结果（幂等）。
    // 浏览器/WebView 可能对同一配对链接发起两次导航（重复提交/重试），
    // 若第二次直接判 invalid，已经把用户带进聊天的结果会被引导页覆盖。
    this.recentlyAccepted = new Map()
    this.stopped = false
    this.tokenSerial = 0
    this.dirty = false
    this.loadPersisted()
  }

  loadPersisted() {
    try {
      const saved = JSON.parse(readFileSync(DEVICES_FILE, 'utf8'))
      if (typeof saved !== 'object' || saved === null) return
      for (const [deviceId, session] of Object.entries(saved)) {
        if (typeof deviceId !== 'string' || typeof session !== 'object' || session === null) continue
        const { createdAt, lastSeenAt, userAgent } = session
        if (typeof createdAt !== 'number' || typeof lastSeenAt !== 'number') continue
        this.devices.set(deviceId, {
          createdAt,
          lastSeenAt,
          ...(typeof userAgent === 'string' ? { userAgent: sanitizeUserAgent(userAgent) } : {}),
        })
      }
      this.clampToMax()
      if (this.evictIdle()) this.persist()
    } catch { /* start empty */ }
  }

  clampToMax() {
    if (this.devices.size <= this.config.maxDevices) return
    const ordered = [...this.devices.entries()].sort((a, b) => a[1].createdAt - b[1].createdAt)
    for (const [id] of ordered.slice(0, this.devices.size - this.config.maxDevices)) this.devices.delete(id)
  }

  evictIdle() {
    const now = Date.now()
    let removed = false
    for (const [id, session] of [...this.devices]) {
      if (now - session.lastSeenAt > this.config.idleExpireMs) {
        this.devices.delete(id)
        removed = true
      }
    }
    return removed
  }

  persist() {
    try {
      mkdirSync(dirname(DEVICES_FILE), { recursive: true })
      const temp = `${DEVICES_FILE}.${process.pid}.${randomBytes(4).toString('hex')}.tmp`
      const payload = {}
      for (const [id, session] of this.devices) payload[id] = session
      writeFileSync(temp, JSON.stringify(payload), { mode: 0o600 })
      try { renameSync(temp, DEVICES_FILE) } catch { writeFileSync(DEVICES_FILE, JSON.stringify(payload)) }
      this.dirty = false
    } catch (err) {
      console.error('wechat-chat: failed to persist paired devices', err)
    }
  }

  issue() {
    const now = Date.now()
    // 允许多枚令牌并存（局域网/公网二维码各一枚）；先清掉已过期的。
    for (const [t, r] of this.tokens) {
      if (now > r.expiresAt) this.tokens.delete(t)
    }
    // 软上限：超过 16 枚时淘汰最旧
    while (this.tokens.size >= 16) {
      let oldest = null
      for (const [t, r] of this.tokens) {
        if (oldest === null || r.issuedAt < oldest.issuedAt) oldest = { t, issuedAt: r.issuedAt }
      }
      if (oldest === null) break
      this.tokens.delete(oldest.t)
    }
    const token = randomBytes(16).toString('hex')
    this.stopped = false
    this.tokenSerial += 1
    this.tokens.set(token, { id: `t${this.tokenSerial}`, issuedAt: now, expiresAt: now + this.config.tokenTtlMs })
    return { token, expiresAt: now + this.config.tokenTtlMs }
  }

  accept(token, userAgent, existingDevice) {
    const record = this.tokens.get(token)
    if (record === undefined || this.stopped || Date.now() > record.expiresAt) {
      // 令牌已被消费但仍在重放窗口内：返回同一个设备，保证配对导航幂等。
      const replay = this.recentlyAccepted.get(token)
      if (replay !== undefined && !this.stopped && Date.now() <= replay.replayUntil
          && this.devices.has(replay.deviceId)) {
        return { ok: true, deviceId: replay.deviceId, replayed: true }
      }
      return { ok: false, code: 'invalid' }
    }
    // 成功配对前先原子消费令牌，防止同一二维码在有效期内被重放。
    this.tokens.delete(token)
    // A fresh QR confirms authorization, but a valid existing cookie already
    // identifies this browser installation. Reuse it; UA/client names alone
    // must never be used to recover or merge an authentication credential.
    if (typeof existingDevice === 'string' && this.touchDevice(existingDevice)) {
      this.rememberAccepted(token, existingDevice, Date.now())
      this.persist()
      return { ok: true, deviceId: existingDevice, reused: true }
    }
    const deviceId = randomBytes(16).toString('hex')
    const now = Date.now()
    if (this.devices.size >= this.config.maxDevices) {
      let oldest
      for (const [id, session] of this.devices) {
        if (oldest === undefined || session.createdAt < oldest.createdAt) oldest = { id, createdAt: session.createdAt }
      }
      if (oldest !== undefined) this.devices.delete(oldest.id)
    }
    const label = sanitizeUserAgent(userAgent)
    this.devices.set(deviceId, {
      createdAt: now,
      lastSeenAt: now,
      ...(label !== undefined ? { userAgent: label } : {}),
    })
    this.rememberAccepted(token, deviceId, now)
    this.persist()
    return { ok: true, deviceId }
  }

  /** 记录刚消费的令牌，供重复导航在短时间内取回同一设备（不新建凭据）。 */
  rememberAccepted(token, deviceId, now) {
    this.recentlyAccepted.set(token, { deviceId, replayUntil: now + REPLAY_WINDOW_MS })
    for (const [t, r] of this.recentlyAccepted) {
      if (now > r.replayUntil) this.recentlyAccepted.delete(t)
    }
    while (this.recentlyAccepted.size > 32) {
      const oldest = this.recentlyAccepted.keys().next().value
      this.recentlyAccepted.delete(oldest)
    }
  }

  stop() {
    this.tokens.clear()
    this.devices.clear()
    this.recentlyAccepted.clear()
    this.persist()
    this.stopped = true
  }

  revoke(deviceId) {
    if (this.stopped) return false
    if (!this.devices.delete(deviceId)) return false
    this.persist()
    return true
  }

  touchDevice(deviceId) {
    const session = this.liveSession(deviceId)
    if (session === undefined) return false
    session.lastSeenAt = Date.now()
    this.dirty = true
    return true
  }

  heartbeat(deviceId) {
    return this.touchDevice(deviceId)
  }

  sweep() {
    if (this.evictIdle() || this.dirty) this.persist()
  }

  hasDevice(deviceId) {
    return this.liveSession(deviceId) !== undefined
  }

  liveSession(deviceId) {
    if (this.stopped) return undefined
    const session = this.devices.get(deviceId)
    if (session === undefined) return undefined
    if (Date.now() - session.lastSeenAt > this.config.idleExpireMs) {
      this.devices.delete(deviceId)
      this.persist()
      return undefined
    }
    return session
  }

  deviceCount() {
    return this.devices.size
  }
}

function sanitizeUserAgent(raw) {
  if (raw === undefined) return undefined
  const cleaned = raw.replace(/[\u0000-\u001F\u007F]/g, ' ').replace(/\s+/g, ' ').trim()
  if (cleaned === '') return undefined
  return cleaned.length <= 180 ? cleaned : cleaned.slice(0, 180)
}

/* ── password-protected public gateway ────────────────────────────────── */

function constantTimeTextEqual(left, right) {
  const a = createHash('sha256').update(String(left), 'utf8').digest()
  const b = createHash('sha256').update(String(right), 'utf8').digest()
  return timingSafeEqual(a, b)
}

export function hashTunnelPassword(password) {
  const salt = randomBytes(16)
  const derived = scryptSync(password, salt, 32)
  return `scrypt$${salt.toString('base64url')}$${derived.toString('base64url')}`
}

function verifyTunnelPassword(password, encoded) {
  const parts = typeof encoded === 'string' ? encoded.split('$') : []
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false
  try {
    const salt = Buffer.from(parts[1], 'base64url')
    const expected = Buffer.from(parts[2], 'base64url')
    const actual = scryptSync(password, salt, expected.length)
    return expected.length === actual.length && timingSafeEqual(expected, actual)
  } catch { return false }
}

function safeNextPath(value) {
  if (typeof value !== 'string' || value.length > 2048 || !value.startsWith('/') || value.startsWith('//')) return '/wechat'
  return value
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch])
}

function readFormBody(req, maxBytes = 8192) {
  return new Promise((resolve) => {
    const chunks = []
    let size = 0
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > maxBytes) { finish(null); req.destroy(); return }
      chunks.push(chunk)
    })
    req.on('end', () => {
      try { finish(new URLSearchParams(Buffer.concat(chunks).toString('utf8'))) } catch { finish(null) }
    })
    req.on('error', () => finish(null))
  })
}

function loginPage(next, error = '') {
  const message = error === '' ? '' : `<div class="error">${escapeHtml(error)}</div>`
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>鲸聊安全登录</title><style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#ededed;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#191919}.card{width:min(90vw,380px);padding:32px 24px;background:#fff;border-radius:14px;box-shadow:0 8px 32px #0002}.logo{width:58px;height:58px;margin:0 auto 14px;display:grid;place-items:center;border-radius:16px;background:#07c160;color:#fff;font-size:31px}h1{margin:0;text-align:center;font-size:22px}.sub{margin:8px 0 24px;text-align:center;color:#777;font-size:14px}.error{margin:0 0 12px;padding:9px 11px;border-radius:7px;background:#fff0f0;color:#c62828;font-size:14px}input{width:100%;height:46px;padding:0 13px;border:1px solid #ccc;border-radius:8px;font-size:16px;outline:none}input:focus{border-color:#07c160}button{width:100%;height:46px;margin-top:14px;border:0;border-radius:8px;background:#07c160;color:#fff;font-size:16px;font-weight:600}</style></head><body><main class="card"><div class="logo">鲸</div><h1>鲸聊安全登录</h1><p class="sub">请输入电脑端设置的公网访问密码</p>${message}<form method="post" action="/__whale/login"><input type="hidden" name="next" value="${escapeHtml(next)}"><input name="password" type="password" autocomplete="current-password" maxlength="256" autofocus required placeholder="访问密码"><button type="submit">登录</button></form></main></body></html>`
}

export class TunnelAuthGateway {
  constructor(upstreamPort, passwordHash, secret) {
    this.upstreamPort = upstreamPort
    this.passwordHash = passwordHash
    // 默认用落盘的密钥：跨重启保持登录态。测试可显式传 secret。
    this.secret = secret instanceof Buffer && secret.length >= 16 ? secret : loadOrCreateTunnelSecret()
    this.authValue = createHmac('sha256', this.secret).update('whale-tunnel-auth-v1').digest('base64url')
    this.server = undefined
    this.failures = new Map()
    this.recent = [] // 最近经过公网网关的请求，用于本机排查「手机扫码没反应」
  }

  /** 记录一次公网请求的结果（只看路径/动作，不记录密码或 Cookie 值）。 */
  record(method, pathname, action, req) {
    const ua = typeof req.headers['user-agent'] === 'string' ? req.headers['user-agent'].slice(0, 90) : ''
    this.recent.push({
      at: new Date().toISOString(),
      method,
      path: String(pathname).slice(0, 200),
      action,
      host: typeof req.headers.host === 'string' ? req.headers.host.slice(0, 120) : '',
      ua,
    })
    if (this.recent.length > 40) this.recent.splice(0, this.recent.length - 40)
  }

  isAuthorized(req) {
    if (this.passwordHash === '') return true
    const value = readCookie(req.headers.cookie, TUNNEL_AUTH_COOKIE)
    return typeof value === 'string' && constantTimeTextEqual(value, this.authValue)
  }

  setPasswordHash(passwordHash) {
    this.passwordHash = passwordHash
    // 换密码时轮换密钥并落盘：所有旧的公网登录立即失效，新登录在重启后继续有效。
    this.secret = randomBytes(32)
    writeTunnelSecret(this.secret)
    this.authValue = createHmac('sha256', this.secret).update('whale-tunnel-auth-v1').digest('base64url')
    this.failures.clear()
  }

  clientKey(req) {
    const cf = req.headers['cf-connecting-ip']
    return typeof cf === 'string' && cf !== '' ? cf.slice(0, 80) : (req.socket.remoteAddress || 'unknown')
  }

  allowAttempt(req) {
    const key = this.clientKey(req)
    const now = Date.now()
    const record = this.failures.get(key)
    if (!record) return true
    if (now < record.lockedUntil) return false
    if (now - record.since > 60_000) {
      this.failures.delete(key)
      return true
    }
    return record.count < 8
  }

  recordFailure(req) {
    const key = this.clientKey(req)
    const now = Date.now()
    const old = this.failures.get(key)
    const record = !old || now - old.since > 60_000 ? { since: now, count: 0, lockedUntil: 0 } : old
    record.count += 1
    if (record.count >= 8) record.lockedUntil = now + 60_000
    this.failures.set(key, record)
    if (this.failures.size > 1024) {
      const oldest = [...this.failures.entries()].sort((a, b) => a[1].since - b[1].since).slice(0, 256)
      for (const [oldKey] of oldest) this.failures.delete(oldKey)
    }
  }

  clearFailures(req) {
    this.failures.delete(this.clientKey(req))
  }

  isAllowedPath(pathname) {
    return pathname === '/wechat'
      || pathname.startsWith('/api/wechat/')
      || pathname === '/api/whale/pair/accept'
      || pathname === '/api/whale/pair/heartbeat'
      || pathname === '/api/whale/pair/status'
  }

  async handle(req, res) {
    const requestUrl = new URL(req.url || '/', 'http://gateway.invalid')
    if (requestUrl.pathname === '/__whale/login') {
      const action = (req.method || 'GET') === 'GET' ? 'login-page' : 'login-submit'
      this.record(req.method || 'GET', requestUrl.pathname, action, req)
      await this.handleLogin(req, res, requestUrl)
      return
    }
    if (!this.isAllowedPath(requestUrl.pathname)) {
      this.record(req.method || 'GET', requestUrl.pathname, 'blocked-path', req)
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
      res.end('not found')
      return
    }
    if (!this.isAuthorized(req)) {
      if ((req.method || 'GET') === 'GET' && requestUrl.pathname === '/wechat') {
        // 直接内联返回登录页（200），不再 303 跳到 /__whale/login：
        // 实测在受限网络下手机能发出第一个请求，但随后的跳转请求会被重置，
        // 于是浏览器停在错误页、用户永远看不到密码框。少一次往返即可避开。
        const next = safeNextPath(req.url || '/wechat')
        this.record(req.method || 'GET', requestUrl.pathname, 'password-inline', req)
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'x-frame-options': 'DENY' })
        res.end(loginPage(next))
      } else {
        this.record(req.method || 'GET', requestUrl.pathname, 'password-required-401', req)
        writeJson(res, 401, { ok: false, code: 'tunnel-password-required' })
      }
      return
    }
    this.record(req.method || 'GET', requestUrl.pathname, 'proxied', req)
    this.proxy(req, res)
  }

  async handleLogin(req, res, requestUrl) {
    if (this.passwordHash === '') {
      res.writeHead(303, { location: safeNextPath(requestUrl.searchParams.get('next') || '/wechat'), 'cache-control': 'no-store' })
      res.end()
      return
    }
    if ((req.method || 'GET') === 'GET') {
      const next = safeNextPath(requestUrl.searchParams.get('next') || '/wechat')
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'x-frame-options': 'DENY' })
      res.end(loginPage(next))
      return
    }
    if (req.method !== 'POST') {
      res.writeHead(405, { allow: 'GET, POST', 'content-type': 'text/plain; charset=utf-8' })
      res.end('method not allowed')
      return
    }
    const form = await readFormBody(req)
    const next = safeNextPath(form && form.get('next'))
    if (!this.allowAttempt(req)) {
      res.writeHead(429, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'retry-after': '60' })
      res.end(loginPage(next, '尝试次数过多，请一分钟后再试'))
      return
    }
    const supplied = form && form.get('password')
    if (typeof supplied !== 'string' || !verifyTunnelPassword(supplied, this.passwordHash)) {
      this.recordFailure(req)
      res.writeHead(401, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
      res.end(loginPage(next, '密码错误'))
      return
    }
    this.clearFailures(req)
    res.writeHead(303, {
      location: next,
      'cache-control': 'no-store',
      'set-cookie': `${TUNNEL_AUTH_COOKIE}=${this.authValue}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${30 * 24 * 60 * 60}`,
    })
    res.end()
  }

  proxy(req, res) {
    const headers = { ...req.headers }
    delete headers.connection
    delete headers['proxy-connection']
    const upstream = httpRequest({
      hostname: '127.0.0.1',
      port: this.upstreamPort,
      method: req.method,
      path: req.url,
      headers,
    }, (upstreamRes) => {
      const responseHeaders = { ...upstreamRes.headers }
      delete responseHeaders.connection
      res.writeHead(upstreamRes.statusCode || 502, responseHeaders)
      upstreamRes.pipe(res)
    })
    upstream.on('error', () => {
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('upstream unavailable')
    })
    req.pipe(upstream)
  }

  start() {
    if (this.server !== undefined) return Promise.reject(new Error('gateway already started'))
    return new Promise((resolve, reject) => {
      const server = createServer((req, res) => {
        void this.handle(req, res).catch(() => {
          if (!res.headersSent) res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
          res.end('gateway error')
        })
      })
      this.server = server
      server.once('error', reject)
      server.listen(0, '127.0.0.1', () => {
        server.removeListener('error', reject)
        const address = server.address()
        if (!address || typeof address === 'string') { reject(new Error('无法取得安全网关端口')); return }
        resolve(`http://127.0.0.1:${address.port}`)
      })
    })
  }

  stop() {
    if (this.server !== undefined) {
      try { this.server.close() } catch { /* best effort */ }
      this.server = undefined
    }
    this.failures.clear()
  }
}

/* ── auto-tunnel (ported from remote-web-ui tunnel.ts; free Cloudflare quick tunnel) ── */

let cloudflaredMod = null
async function loadCloudflared() {
  if (cloudflaredMod !== null) return cloudflaredMod
  const mod = await import(pathToFileURL('__RUNTIME_ROOT__/tools/whale/cloudflared/lib/lib.js').href)
  cloudflaredMod = (mod && mod.default) ?? mod
  return cloudflaredMod
}

// cloudflared 二进制放到 node_modules 之外运行，避免 pnpm 重装插件时锁文件
const CLOUDFLARED_BIN = '__RUNTIME_ROOT__/tools/whale/cloudflared.exe'

// 连接器就绪轮询间隔：查 cloudflared 本地 /ready，判断是否已注册到 Cloudflare 边缘。
const TUNNEL_READY_INTERVAL_MS = 1_500
// 连接器持续未能就绪多久后重建隧道（重建会换地址，尽量少换）。
const TUNNEL_REBUILD_AFTER_MS = 60_000
// Cloudflare 偶尔会先注册连接器、稍后才发布隧道的 DNS 记录（实测可能等 1~3 分钟）：
// 期间手机解析该域名会得到 NXDOMAIN（浏览器报 ERR_NAME_NOT_RESOLVED），所以出码前
// 必须确认域名已发布。两个要点：
//   1) 任一解析器返回 Status=0 且有 A 记录即算已发布；
//   2) 解析器会缓存 NXDOMAIN（否定缓存）—— 刚创建时查过一次就会一直返回 NXDOMAIN，
//      所以必须挑"结果新且本机可达"的解析器：实测 doh.pub 可靠，阿里常给旧答案。
const TUNNEL_DOH_ENDPOINTS = [
  'https://doh.pub/dns-query',
  'https://dns.alidns.com/resolve',
  'https://dns.google/resolve',
]

/** 该域名是否已在公网 DNS 发布（Status=0 且存在 A 记录）。 */
export async function tunnelNamePublished(hostname, endpoints = TUNNEL_DOH_ENDPOINTS) {
  if (typeof hostname !== 'string' || hostname === '') return false
  for (const endpoint of endpoints) {
    try {
      const res = await fetch(`${endpoint}?name=${encodeURIComponent(hostname)}&type=A`, {
        signal: AbortSignal.timeout(4_000),
        headers: { accept: 'application/dns-json', 'user-agent': 'dsh-wechat-chat-publish' },
      })
      if (!res.ok) continue
      const body = await res.json()
      if (Number(body && body.Status) === 0 && Array.isArray(body.Answer)
          && body.Answer.some(item => item && Number(item.type) === 1 && typeof item.data === 'string')) {
        return true
      }
    } catch { /* 换下一个解析器 */ }
  }
  return false
}

/** 找一个空闲的回环端口，交给 cloudflared 作为本地 metrics/ready 端口。 */
function freeLoopbackPort() {
  return new Promise((resolve) => {
    const probe = createServer()
    probe.once('error', () => resolve(0))
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address()
      const port = address && typeof address === 'object' ? address.port : 0
      probe.close(() => resolve(port))
    })
  })
}

async function ensureCloudflaredBin(cf) {
  if (!existsSync(CLOUDFLARED_BIN)) {
    mkdirSync(dirname(CLOUDFLARED_BIN), { recursive: true })
    const src = typeof cf.bin === 'string' ? cf.bin : ''
    if (src !== '' && existsSync(src)) {
      copyFileSync(src, CLOUDFLARED_BIN)
    } else {
      await cf.install(CLOUDFLARED_BIN)
    }
  }
  // 让 cloudflared 包使用数据目录里的二进制
  if (typeof cf.use === 'function') cf.use(CLOUDFLARED_BIN)
  return CLOUDFLARED_BIN
}

export class TunnelManager {
  constructor(target) {
    this.target = target
    this.phase = 'stopped'
    this.url = undefined
    this.error = undefined
    // 连接器就绪状态（本地 /ready），用于快速判定能否出码
    this.connected = false
    this.readyFailures = 0
    this.readyTimer = undefined
    this.readySince = undefined
    this.metricsPort = undefined
    // 隧道域名是否已被公网 DNS 发布（未发布则手机一定连不上）
    this.published = false
    this.publishedSince = undefined
    this.publishTimer = undefined
    this.handle = undefined
    this.timers = []
    this.generation = 0
    this.stopping = false
    this.listeners = new Set()
  }

  onPhase(fn) {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }

  info() {
    return {
      phase: this.phase,
      ...(this.url !== undefined ? { url: this.url } : {}),
      ...(this.url !== undefined ? { connected: this.connected } : {}),
      ...(this.url !== undefined ? { published: this.published } : {}),
      ...(this.error !== undefined ? { error: this.error } : {}),
    }
  }

  schedulePublish(delayMs) {
    clearTimeout(this.publishTimer)
    this.publishTimer = setTimeout(() => { void this.runPublishCheck() }, delayMs)
  }

  /**
   * 确认隧道域名已在公网发布。Cloudflare 发布新的快隧道域名可能要 1~3 分钟，
   * 所以这里**只耐心等待、绝不重建**：重建会换域名并重新排队，反而永远等不到发布。
   * 只有连接器连不上边缘（runReadyCheck）才值得重建。
   */
  async runPublishCheck() {
    if (this.stopping || this.phase !== 'running' || this.url === undefined) return
    const hostname = String(this.url).replace(/^https?:\/\//, '').replace(/\/.*$/, '')
    const ok = await tunnelNamePublished(hostname)
    if (this.stopping || this.phase !== 'running') return
    if (ok) {
      this.publishedSince = undefined
      if (!this.published) { this.published = true; this.emit() }
      this.schedulePublish(30_000)
      return
    }
    if (this.publishedSince === undefined) this.publishedSince = Date.now()
    // 前 2 分钟每 5 秒看一次，之后每 20 秒看一次；始终复用同一个域名
    const waited = Date.now() - this.publishedSince
    this.schedulePublish(waited < 120_000 ? 5_000 : 20_000)
  }

  /**
   * 连接器是否已注册到 Cloudflare 边缘 —— 查 cloudflared 本地 /ready。
   * 这是"手机能不能连上"的权威且极快的信号（毫秒级、不受本机代理影响）。
   */
  async connectorReadyConnections() {
    // 没拿到本地 metrics 端口时无法判定，按"可用"处理，避免误挡出码
    if (this.metricsPort === undefined || this.metricsPort <= 0) return 1
    try {
      const res = await fetch(`http://127.0.0.1:${this.metricsPort}/ready`, {
        signal: AbortSignal.timeout(2_000),
        headers: { 'user-agent': 'dsh-wechat-chat-health' },
      })
      if (!res.ok) return 0
      const body = await res.json()
      const count = Number(body && body.readyConnections)
      return Number.isFinite(count) && count > 0 ? count : 0
    } catch {
      return 0
    }
  }

  scheduleReady(delayMs) {
    clearTimeout(this.readyTimer)
    this.readyTimer = setTimeout(() => { void this.runReadyCheck() }, delayMs)
  }

  async runReadyCheck() {
    if (this.stopping || this.phase !== 'running') return
    const count = await this.connectorReadyConnections()
    if (this.stopping || this.phase !== 'running') return
    if (count > 0) {
      this.readyFailures = 0
      this.readySince = undefined
      if (!this.connected) { this.connected = true; this.emit() }
      this.scheduleReady(10_000)
      return
    }
    this.readyFailures += 1
    if (this.connected) { this.connected = false; this.emit() }
    if (this.readySince === undefined) this.readySince = Date.now()
    // 连接器长时间注册不上边缘才重建（本地判定，不依赖公网探测）
    if (Date.now() - this.readySince >= TUNNEL_REBUILD_AFTER_MS) {
      this.fail('隧道连接器无法注册到 Cloudflare，正在重建')
      return
    }
    this.scheduleReady(TUNNEL_READY_INTERVAL_MS)
  }

  emit() {
    const info = this.info()
    for (const fn of this.listeners) {
      try { fn(info) } catch { /* keep emitting */ }
    }
  }

  start() {
    if (this.phase === 'starting' || this.phase === 'running') return
    this.teardown()
    this.stopping = false
    this.generation += 1
    void this.attempt()
  }

  stop() {
    this.teardown()
    this.url = undefined
    this.error = undefined
    this.connected = false
    this.readyFailures = 0
    this.readySince = undefined
    this.published = false
    this.publishedSince = undefined
    this.setPhase('stopped')
  }

  dispose() {
    this.stop()
  }

  async attempt() {
    if (this.stopping) return
    const gen = this.generation
    this.setPhase('starting')
    try {
      const cf = await loadCloudflared()
      await ensureCloudflaredBin(cf)
      if (this.stopping || gen !== this.generation) return
      // 固定一个本地 metrics 端口，便于用 /ready 毫秒级判断连接器是否就绪
      this.metricsPort = await freeLoopbackPort()
      const options = { '--no-autoupdate': true, '--protocol': 'http2' }
      if (this.metricsPort > 0) options['--metrics'] = `127.0.0.1:${this.metricsPort}`
      const handle = cf.Tunnel.quick(this.target, options)
      this.handle = handle
      const urlTimer = setTimeout(() => { this.fail('等待隧道地址超时') }, 30_000)
      this.timers.push(urlTimer)
      handle.on('url', (value) => {
        if (this.handle !== handle) return
        clearTimeout(urlTimer)
        this.url = value
        this.error = undefined
        this.connected = false
        this.readyFailures = 0
        this.readySince = undefined
        this.published = false
        this.publishedSince = undefined
        this.setPhase('running')
        this.scheduleReady(500)
        this.schedulePublish(1_000)
      })
      handle.on('exit', () => {
        if (this.handle !== handle) return
        if (this.stopping) return
        this.fail('隧道进程意外退出')
      })
      handle.on('error', (value) => {
        if (this.handle !== handle || this.phase !== 'starting') return
        this.error = value instanceof Error ? value.message : String(value)
      })
    } catch (err) {
      if (this.stopping || gen !== this.generation) return
      this.fail(`无法获取 cloudflared 二进制：${(err && err.message) || String(err)}`)
    }
  }

  fail(message) {
    if (this.stopping) return
    this.url = undefined
    this.error = message
    this.connected = false
    this.readyFailures = 0
    this.readySince = undefined
    this.published = false
    this.publishedSince = undefined
    clearTimeout(this.readyTimer)
    clearTimeout(this.publishTimer)
    if (this.handle !== undefined) {
      try { this.handle.stop() } catch { /* best effort */ }
      this.handle = undefined
    }
    this.setPhase('failed')
    const retry = setTimeout(() => {
      if (!this.stopping && this.phase === 'failed') void this.attempt()
    }, 10_000)
    this.timers.push(retry)
  }

  teardown() {
    this.stopping = true
    for (const t of this.timers) clearTimeout(t)
    this.timers = []
    clearTimeout(this.readyTimer)
    this.readyTimer = undefined
    clearTimeout(this.publishTimer)
    this.publishTimer = undefined
    if (this.handle !== undefined) {
      try { this.handle.stop() } catch { /* best effort */ }
      this.handle = undefined
    }
  }

  setPhase(phase) {
    this.phase = phase
    this.emit()
  }
}

const HIDDEN_FILE = join(home, 'wechat-chat-hidden.json')

function loadHidden() {
  try { return JSON.parse(readFileSync(HIDDEN_FILE, 'utf8')) } catch { return {} }
}

function persistHidden(hidden) {
  try {
    mkdirSync(dirname(HIDDEN_FILE), { recursive: true })
    writeFileSync(HIDDEN_FILE, JSON.stringify(hidden))
  } catch (err) {
    console.error('wechat-chat: failed to persist hidden ids', err)
  }
}

function hiddenIdsOf(key) {
  const hidden = loadHidden()
  const arr = hidden[key]
  return Array.isArray(arr) ? arr : []
}

/* ── 聊天对象昵称（可修改，持久化） ─────────── */

const NICKNAME_FILE = join(home, 'wechat-chat-settings.json')

function loadChatSettings() {
  try {
    const value = JSON.parse(readFileSync(NICKNAME_FILE, 'utf8'))
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { return {} }
}

function persistChatSettings(patch) {
  try {
    mkdirSync(dirname(NICKNAME_FILE), { recursive: true })
    const value = { ...loadChatSettings(), ...patch }
    writeFileSync(NICKNAME_FILE, JSON.stringify(value), { mode: 0o600 })
    return true
  } catch (err) {
    console.error('wechat-chat: failed to persist settings', err)
    return false
  }
}

function loadNickname() {
  const value = loadChatSettings().nickname
  return typeof value === 'string' && value.trim() !== '' ? value.trim().slice(0, 30) : undefined
}

function persistNickname(name) {
  persistChatSettings({ nickname: name })
}

function loadTunnelPasswordState() {
  const settings = loadChatSettings()
  const present = Object.prototype.hasOwnProperty.call(settings, 'tunnelPasswordHash')
  const value = settings.tunnelPasswordHash
  const passwordHash = typeof value === 'string' && (value === '' || value.startsWith('scrypt$')) ? value : ''
  return { present, passwordHash }
}

function persistTunnelPasswordHash(passwordHash) {
  return persistChatSettings({ tunnelPasswordHash: passwordHash, tunnelPassword: undefined })
}

/**
 * 公网登录 cookie 的 HMAC 密钥。必须跨重启保持不变：以前每次启动随机生成，
 * 结果是 DSH 一重启，手机上已登录的会话全部 401（表现为“发送失败”）。
 * 换密码时会重新生成并落盘，从而让所有旧登录立即失效。
 */
const TUNNEL_SECRET_FILE = join(home, 'wechat-chat', 'tunnel-secret')

function readTunnelSecret() {
  try {
    const saved = readFileSync(TUNNEL_SECRET_FILE, 'utf8').trim()
    if (/^[0-9a-f]{64}$/i.test(saved)) return Buffer.from(saved, 'hex')
  } catch { /* 首次运行或文件损坏 */ }
  return undefined
}

function writeTunnelSecret(secret) {
  try {
    mkdirSync(dirname(TUNNEL_SECRET_FILE), { recursive: true })
    const temp = `${TUNNEL_SECRET_FILE}.${String(process.pid)}.tmp`
    writeFileSync(temp, secret.toString('hex'), { mode: 0o600 })
    renameSync(temp, TUNNEL_SECRET_FILE)
  } catch { /* 落盘失败时退回内存密钥，不影响本次运行 */ }
}

function loadOrCreateTunnelSecret() {
  const saved = readTunnelSecret()
  if (saved !== undefined) return saved
  const secret = randomBytes(32)
  writeTunnelSecret(secret)
  return secret
}

export function funnelArgs(port) {
  if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Invalid local gateway port');
  return ['funnel','--bg',String(port)];
}

export function createCliOutput(limit=2*1024*1024) {
  let text='';
  return {add(chunk){text+=String(chunk);if(text.length>limit)throw new Error('Tailscale response too large');},value(){return text;}};
}

export function sendAvatarAsset(req,res,data,type) {
  const etag='"'+createHash('sha256').update(data).digest('hex')+'"';
  const headers={'content-type':type,'cache-control':'private, max-age=300, must-revalidate','etag':etag,'vary':'Cookie'};
  if(req.headers['if-none-match']===etag){res.writeHead(304,headers);res.end();return;}
  res.writeHead(200,{...headers,'content-length':Buffer.byteLength(data)});res.end(data);
}

export function startGateway(host, config = {}) {
  const assistantBridge = createAssistantBridge(process.env.WHALE_RUNTIME_ROOT)
  const defaultNickname = config.nickname || DEFAULT_NICKNAME
  // 默认复用已有对话：重装 App 或换设备后 client 变化时，不再新建会话。
  const service = new PairingService({
    tokenTtlMs: config.tokenTtlMs || DEFAULT_TOKEN_TTL_MS,
    idleExpireMs: config.idleExpireMs || DEFAULT_IDLE_EXPIRE_MS,
    offlineAfterMs: config.offlineAfterMs || DEFAULT_OFFLINE_AFTER_MS,
    maxDevices: config.maxDevices || DEFAULT_MAX_DEVICES,
  })
  let publicBaseUrl = typeof config.publicBaseUrl === 'string' && config.publicBaseUrl !== ''
    ? config.publicBaseUrl
    : undefined
  const autoTunnel = config.autoTunnel === true
  // 固定地址模式：把带密码的本地网关挂到 Tailscale Funnel（域名固定、重启不变）。
  const funnelEnabled = config.funnel === true
  const configuredTunnelPassword = typeof config.tunnelPassword === 'string' ? config.tunnelPassword : ''
  const storedPassword = loadTunnelPasswordState()
  let tunnelPasswordHash = storedPassword.passwordHash
  // 兼容旧配置：仅在从未写入交互式设置时迁移一次。显式关闭（空哈希）不会在重启后被旧配置重新开启。
  if (!storedPassword.present && configuredTunnelPassword !== '') {
    tunnelPasswordHash = hashTunnelPassword(configuredTunnelPassword)
    persistTunnelPasswordHash(tunnelPasswordHash)
    console.warn('wechat-chat: 已把旧 tunnelPassword 迁移为 scrypt 哈希；请从 cordis.patch.yml 删除明文字段')
  }
  const webPort = Number.isFinite(host.webServer.port) ? host.webServer.port : 3080
  assistantBridge.announce(webPort)

  // Free Cloudflare quick tunnel. When a password is configured, cloudflared
  // targets a loopback-only gateway that authenticates first and exposes only
  // the Whale Chat routes — never the rest of the DSH Web application.
  let tunnelBase = undefined
  const tunnel = new TunnelManager(`http://127.0.0.1:${webPort}`)
  // 即使暂未设置密码，也始终通过路径白名单网关，避免暴露整个 DSH Web。
  const authGateway = new TunnelAuthGateway(webPort, tunnelPasswordHash)
  // 语音条音频：保留 7 天后自动清理（含索引记录）。
  const voice = new VoiceStore(join(home, 'wechat-chat', 'voice'))
  const voiceLanguage = config.stt && ['auto', 'zh', 'en'].includes(config.stt.language) ? config.stt.language : 'zh'
  const stt = new LocalSpeech()
  tunnel.onPhase((info) => {
    tunnelBase = info.phase === 'running' && typeof info.url === 'string' && info.url !== '' ? info.url : undefined
  })

  let voiceUploadActive = false // 上传串行化：同时只缓冲一段录音，限制内存占用

  const lanHosts = () => lanIPv4Addresses()
  const trustedHosts = () => {
    const hosts = lanHosts()
    const publicHost = publicHostOf(publicBaseUrl)
    if (publicHost !== undefined) hosts.push(publicHost)
    const tunnelHost = publicHostOf(tunnelBase)
    if (tunnelHost !== undefined) hosts.push(tunnelHost)
    return hosts
  }
  const loopbackFence = (req) => isLoopbackRequest(req)
  const lanFence = (req) => isTrustedApiRequest(req, trustedHosts())

  const deviceIdOf = (req) => {
    const cookie = readCookie(req.headers.cookie, COOKIE_NAME)
    if (cookie) return cookie
    const header = req.headers[DEVICE_HEADER]
    if (typeof header === 'string' && header !== '') return header
    return undefined
  }

  /** Non-loopback /api/wechat gate: a live device cookie is the access control. */
  const gateOk = (req, queryDevice) => {
    if (isLoopbackRequest(req)) return true
    const credential = deviceIdOf(req)
    if (credential !== undefined && queryDevice !== undefined && credential !== queryDevice) return false
    const id = credential || queryDevice
    if (id === undefined) return false
    return service.touchDevice(id)
  }

  /* ── 语音条：后台串行识别，完成后自动作为用户消息送入真实会话 ─────── */

  let voiceQueue = Promise.resolve()

  function enqueueVoice(job) {
    voiceQueue = voiceQueue.then(() => processVoice(job)).catch(() => { /* 单条失败不阻塞后续 */ })
    return voiceQueue
  }

  async function processVoice({ id, wav, sessionKey, deviceId }) {
    try {
      const result = await stt.transcribe(wav, voiceLanguage)
      const text = result.text.trim()
      if (text === '') throw Object.assign(new Error('没有识别到语音内容'), { code: 'no-speech' })
      voice.update(id, { status: 'transcribed', transcript: text, durationMs: result.durationMs })
      if (!deviceId || !service.touchDevice(deviceId)) throw new Error('配对设备已失效')
      await assistantBridge.receive(deviceId, `【语音】${text}`, voiceMessageId(id))
      voice.update(id, { status: 'sent' })
    } catch (error) {
      voice.update(id, { status: 'failed', error: (error && error.message) || '语音识别失败' })
    }
  }

  /* ── routes ─────────────────────────────────────────────────────────── */

  const cookieHeader = (deviceId) => ({
    'set-cookie': [
      `${COOKIE_NAME}=${deviceId}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${COOKIE_MAX_AGE_SEC}`,
    ],
  })

  /** The QR link base: active tunnel URL first, then the configured public
   *  base, then the first LAN literal. */
  const pairBase = () => {
    if (autoTunnel && tunnelBase !== undefined) return tunnelBase
    if (publicBaseUrl !== undefined) return publicBaseUrl
    const lan = lanIPv4Addresses()
    const port = Number.isFinite(host.webServer.port) ? host.webServer.port : undefined
    if (lan.length === 0 || port === undefined) return undefined
    return `http://${lan[0]}:${String(port)}`
  }

  const handlePage = async (req, res) => {
    const url = new URL(req.url || '/', 'http://wechat.invalid')
    const pairToken = url.searchParams.get('pair')
    const client = url.searchParams.get('client') || ''
    const render = (deviceId, pairErr) => PAGE
      .replace('class="guide" id="guide"', () => deviceId && !pairErr ? 'class="guide hidden" id="guide"' : 'class="guide" id="guide"')
      .replace('__WECHAT_DEVICE_VALUE__', () => JSON.stringify(deviceId))
      .replace('__WECHAT_PAIR_ERR_VALUE__', () => JSON.stringify(pairErr))
      .replace('__WECHAT_CLIENT_VALUE__', () => JSON.stringify(client))
    // 导航式配对：/wechat?pair=<token> 校验后下发设备 Cookie，再重定向到不含令牌的干净地址。
    if (typeof pairToken === 'string' && pairToken !== '') {
      const ra = (req.socket && req.socket.remoteAddress) || '?'
      console.log(`wechat-chat: pair accept from ${ra} host=${req.headers.host || '?'} ua=${(req.headers['user-agent'] || '').slice(0, 60)}`)
      if (!lanFence(req)) {
        console.log(`wechat-chat: pair accept REFUSED (fence) from ${ra} host=${req.headers.host || '?'}`)
        res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' })
        res.end('forbidden')
        return
      }
      const ua = req.headers['user-agent']
      const result = service.accept(pairToken, typeof ua === 'string' ? ua : undefined, readCookie(req.headers.cookie, COOKIE_NAME))
      if (!result.ok) {
        console.log(`wechat-chat: pair accept FAILED code=${result.code}`)
        // 令牌失效/过期：下发引导页并带提示，刷新二维码重扫即可
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
        res.end(render('', result.code))
        return
      }
      console.log(`wechat-chat: pair accept OK device=${result.deviceId.slice(0, 8)}…`)
      // Register the freshly paired device with the assistant channel immediately.
      // Pairing already consumed a one-time token and the tunnel password; without
      // this the first chat load shows the unpaired guide and the management page
      // returns 403 until the desktop user remembers "同步新配对设备".
      try {
        if (assistantBridge.installed()) await assistantBridge.enroll(result.deviceId)
      } catch (err) {
        console.error(`wechat-chat: device auto-enroll failed: ${(err && err.message) || String(err)}`)
      }
      const cleanLocation = client !== '' ? `/wechat?client=${encodeURIComponent(client)}` : '/wechat'
      res.writeHead(303, {
        location: cleanLocation,
        'cache-control': 'no-store',
        'referrer-policy': 'no-referrer',
        ...cookieHeader(result.deviceId),
      })
      res.end()
      return
    }
    const device = url.searchParams.get('device') || deviceIdOf(req) || ''
    console.log(`wechat-chat: /wechat served to ${(req.socket && req.socket.remoteAddress) || '?'} host=${req.headers.host || '?'} device=${device ? device.slice(0, 8) : 'none'} client=${client ? client.slice(0, 8) : 'none'}`)
    // App（WebView）自带扫码引导页：未配对时直接 403，让它回落到本地引导页，
    // 避免再弹出一个多余的服务端「连接电脑上的鲸聊」页面。
    if (device === '' && /;\s*wv\)/.test(String(req.headers['user-agent'] || ''))) {
      res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
      res.end('unpaired')
      return
    }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
    res.end(render(device, ''))
  }

  /** Desktop pairing panel: QR + link + stop. Loopback-only. */
  const handlePanel = async (req, res) => {
    if (!loopbackFence(req)) {
      res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('forbidden: panel is local-only')
      return
    }
    const passwordEnabled = tunnelPasswordHash !== ''
    // 每次请求都从磁盘读面板：改文案/状态区不用重启 DSH。
    let panelSource = PANEL
    try {
      panelSource = readFileSync(fileURLToPath(new URL('./panel-page.html', import.meta.url)), 'utf8')
    } catch { /* 回退到启动时缓存的版本 */ }
    const html = panelSource
      .replace('__QRCODE_LIB__', () => QRCODE_LIB)
      .replace('__TUNNEL_PASSWORD_SUB__', passwordEnabled ? '，并启用密码保护' : '')
      .replace('__TUNNEL_PASSWORD_HINT__', passwordEnabled ? '首次扫码先输入公网登录密码，再完成配对。' : '一次扫码即完成配对。')
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
    res.end(html)
  }

  const handleWhaleApi = async (req, res) => {
    const url = new URL(req.url || '/', 'http://wechat.invalid')
    const pathname = url.pathname

    if (pathname === '/api/whale/pair/security') {
      if (!loopbackFence(req)) { writeJson(res, 403, { ok: false, code: 'forbidden' }); return }
      if (req.method === 'GET') {
        writeJson(res, 200, { ok: true, passwordEnabled: tunnelPasswordHash !== '', minimumLength: 8 })
        return
      }
      if (req.method !== 'POST') { writeJson(res, 405, { ok: false, code: 'method-not-allowed' }); return }
      const body = await readJsonBody(req, MAX_BODY)
      const password = body && typeof body.password === 'string' ? body.password : null
      if (password === null || password.length > 256 || (password !== '' && password.length < 8)) {
        writeJson(res, 400, { ok: false, code: 'bad-password', error: '密码至少 8 位；留空仅用于明确关闭密码保护' })
        return
      }
      const passwordHash = password === '' ? '' : hashTunnelPassword(password)
      if (!persistTunnelPasswordHash(passwordHash)) {
        writeJson(res, 500, { ok: false, code: 'persist-failed', error: '无法保存密码设置' })
        return
      }
      tunnelPasswordHash = passwordHash
      authGateway.setPasswordHash(passwordHash)
      writeJson(res, 200, { ok: true, passwordEnabled: password !== '', sessionsInvalidated: true })
      return
    }

    if (pathname === '/api/whale/stt/status') {
      if (!loopbackFence(req) || req.method !== 'GET') { writeJson(res, req.method === 'GET' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      writeJson(res, 200, { ok: true, stt: stt.info() })
      return
    }

    if (pathname === '/api/whale/stt/install') {
      if (!loopbackFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      const started = stt.startInstall()
      writeJson(res, started ? 202 : 409, { ok: started, stt: stt.info(), ...(started ? {} : { error: stt.ready() ? '离线模型已经安装' : '安装已在进行中' }) })
      return
    }

    if (pathname === '/api/whale/stt/cancel') {
      if (!loopbackFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      writeJson(res, 200, { ok: true, cancelled: stt.cancelInstall(), stt: stt.info() })
      return
    }

    if (pathname === '/api/whale/pair/issue') {
      if (!loopbackFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      // 只走公网：隧道优先，其次手动 publicBaseUrl
      const base = (autoTunnel && tunnelBase !== undefined) ? tunnelBase : (publicBaseUrl || undefined)
      if (base === undefined) {
        writeJson(res, 409, { ok: false, code: 'tunnel-required', error: '公网隧道未就绪或未配置公网地址' })
        return
      }
      // 出码条件：连接器已注册到边缘，且域名确实已在公网发布。
      // 只注册连接器而没发布 DNS 时，手机解析该域名会得到 NXDOMAIN，务必挡住。
      if (base === tunnelBase && !tunnel.connected) {
        writeJson(res, 409, { ok: false, code: 'tunnel-not-connected', error: '公网隧道还在连接 Cloudflare，请稍候几秒', tunnel: tunnel.info() })
        return
      }
      if (base === tunnelBase && !tunnel.published) {
        writeJson(res, 409, { ok: false, code: 'tunnel-not-published', error: 'Cloudflare 还没发布该隧道域名，正在重试；稍候再刷新二维码', tunnel: tunnel.info() })
        return
      }
      const { token, expiresAt } = service.issue()
      writeJson(res, 200, {
        ok: true,
        mode: 'public',
        token,
        expiresAt,
        url: `${base}/wechat?pair=${token}`,
        ...(publicBaseUrl !== undefined ? { publicBaseUrl } : {}),
        tunnel: tunnel.info(),
      })
      return
    }

    if (pathname === '/api/whale/pair/accept') {
      if (!lanFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      const body = await readJsonBody(req, MAX_BODY)
      const token = body && typeof body.token === 'string' ? body.token : ''
      if (token === '') { writeJson(res, 400, { ok: false, code: 'bad-payload' }); return }
      const ua = req.headers['user-agent']
      const result = service.accept(token, typeof ua === 'string' ? ua : undefined, readCookie(req.headers.cookie, COOKIE_NAME))
      if (!result.ok) {
        writeJson(res, 404, { ok: false, code: result.code })
        return
      }
      try {
        if (assistantBridge.installed()) await assistantBridge.enroll(result.deviceId)
      } catch (err) {
        console.error('wechat-chat: device auto-enroll failed')
        writeJson(res, 503, { ok: false, code: 'enroll-failed' }); return
      }
      writeJson(res, 200, { ok: true, deviceId: result.deviceId }, cookieHeader(result.deviceId))
      return
    }

    if (pathname === '/api/whale/pair/stop') {
      if (!loopbackFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      service.stop()
      writeJson(res, 200, { ok: true })
      return
    }

    if (pathname === '/api/whale/pair/revoke') {
      if (!loopbackFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      const body = await readJsonBody(req, MAX_BODY)
      const deviceId = body && typeof body.deviceId === 'string' ? body.deviceId : ''
      if (deviceId === '') { writeJson(res, 400, { ok: false, code: 'bad-payload' }); return }
      writeJson(res, 200, { ok: true, revoked: service.revoke(deviceId) })
      return
    }

    if (pathname === '/api/whale/pair/heartbeat') {
      if (!lanFence(req) || req.method !== 'POST') { writeJson(res, req.method === 'POST' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      const deviceId = readCookie(req.headers.cookie, COOKIE_NAME)
      if (deviceId === undefined || !service.heartbeat(deviceId)) {
        writeJson(res, 401, { ok: false, code: 'unpaired' })
        return
      }
      writeJson(res, 200, { ok: true })
      return
    }

    if (pathname === '/api/whale/pair/tunnel') {
      if (!loopbackFence(req) || req.method !== 'GET') { writeJson(res, req.method === 'GET' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      writeJson(res, 200, {
        ok: true,
        autoTunnel,
        ...(publicBaseUrl !== undefined ? { publicBaseUrl } : {}),
        tunnel: tunnel.info(),
        ...(funnelEnabled ? { funnel: { enabled: funnelState.enabled, error: funnelState.error, url: publicBaseUrl, publicReachable:funnelState.publicReachable, publicState:funnelState.publicState, checkedAt:funnelState.checkedAt } } : {}),
        deviceCount: service.deviceCount(),
      })
      return
    }

    // 排查用：最近经过公网网关的请求（仅本机可读，不含密码与 Cookie 值）。
    if (pathname === '/api/whale/tunnel/log') {
      if (!loopbackFence(req) || req.method !== 'GET') { writeJson(res, req.method === 'GET' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      writeJson(res, 200, { ok: true, requests: authGateway.recent })
      return
    }

    if (pathname === '/api/whale/pair/status') {
      if (!lanFence(req) || req.method !== 'GET') { writeJson(res, req.method === 'GET' ? 403 : 405, { ok: false, code: 'forbidden' }); return }
      const deviceId = readCookie(req.headers.cookie, COOKIE_NAME)
      const paired = deviceId !== undefined && service.hasDevice(deviceId)
      writeJson(res, 200, {
        ok: true,
        paired,
        lanAvailable: lanIPv4Addresses().length > 0,
        lanAddresses: lanIPv4Addresses(),
        ...(paired ? { deviceCount: service.deviceCount() } : {}),
      })
      return
    }

    writeJson(res, 404, { ok: false, code: 'not-found' })
  }

  const handleWechatApi = async (req, res) => {
    const url = new URL(req.url || '/', 'http://wechat.invalid')
    const pathname = url.pathname
    const queryDevice = url.searchParams.get('device') || undefined
    const client = url.searchParams.get('client') || undefined
    const credentialDevice = deviceIdOf(req) || undefined
    const device = credentialDevice || queryDevice
    // 会话键优先用 App 内持久化的 client：设备 id 每次重新配对都会变，
    // 只有 client 能跨扫码/换公网地址稳定指向同一个对话。
    const key = client || device || undefined

    // Paired owner traffic enters the assistant queue, never two model runtimes.
    if (assistantBridge.installed() && (['/api/wechat/state','/api/wechat/send','/api/wechat/poll','/api/wechat/cancel'].includes(pathname) || pathname.startsWith('/api/wechat/assistant/'))) {
      if (!lanFence(req) || !gateOk(req, queryDevice)) { writeJson(res, 403, { ok:false, error:'设备或来源校验失败' }); return }
      await assistantBridge.handle({req,res,device,live:id=>service.touchDevice(id),readJsonBody,readRawBody,writeJson,sttInfo:stt.info(),nickname:loadNickname()||defaultNickname,hiddenIds:hiddenIdsOf(key)})
      return
    }

    if (pathname === '/api/wechat/avatar/other' || pathname === '/api/wechat/avatar/me') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      const who = pathname.endsWith('/other') ? 'other' : 'me'
      for (const ext of ['png', 'svg', 'jpg', 'jpeg', 'webp']) {
        const file = join(AVATAR_DIR, `${who}.${ext}`)
        if (existsSync(file)) {
          const types = { png: 'image/png', svg: 'image/svg+xml', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' }
          sendAvatarAsset(req,res,readFileSync(file),types[ext])
          return
        }
      }
      sendAvatarAsset(req,res,who==='other'?ASSISTANT_SVG:USER_SVG,'image/svg+xml')
      return
    }

    if (pathname === '/api/wechat/avatar/upload') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      const body = await readJsonBody(req, 16 * 1024 * 1024)
      const side = body && (body.side === 'me' || body.side === 'other') ? body.side : null
      const data = body && typeof body.data === 'string' ? body.data : ''
      const m = /^data:image\/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(data)
      if (!side || m === null) { writeJson(res, 400, { ok: false, error: '无效的图片数据' }); return }
      try {
        const ext = m[1] === 'jpeg' ? 'jpg' : m[1]
        const buf = Buffer.from(m[2], 'base64')
        if (buf.length > 8 * 1024 * 1024) { writeJson(res, 400, { ok: false, error: '图片过大' }); return }
        mkdirSync(AVATAR_DIR, { recursive: true })
        // 删除同侧旧头像，避免旧文件优先级更高
        for (const old of ['png', 'svg', 'jpg', 'jpeg', 'webp']) {
          const f = join(AVATAR_DIR, `${side}.${old}`)
          try { if (existsSync(f)) unlinkSync(f) } catch { /* ignore */ }
        }
        writeFileSync(join(AVATAR_DIR, `${side}.${ext}`), buf)
        writeJson(res, 200, { ok: true, side })
      } catch (err) {
        writeJson(res, 500, { ok: false, error: (err && err.message) || '保存失败' })
      }
      return
    }

    if (pathname === '/api/wechat/background') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      for (const ext of ['png', 'jpg', 'jpeg', 'webp']) {
        const f = join(AVATAR_DIR, `background.${ext}`)
        if (existsSync(f)) {
          const types = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' }
          res.writeHead(200, { 'content-type': types[ext], 'cache-control': 'no-cache' })
          res.end(readFileSync(f))
          return
        }
      }
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('no background')
      return
    }

    if (pathname === '/api/wechat/background/upload') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      const body = await readJsonBody(req, 16 * 1024 * 1024)
      const data = body && typeof body.data === 'string' ? body.data : ''
      const m = /^data:image\/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(data)
      if (m === null) { writeJson(res, 400, { ok: false, error: '无效的图片数据' }); return }
      try {
        const ext = m[1] === 'jpeg' ? 'jpg' : m[1]
        const buf = Buffer.from(m[2], 'base64')
        if (buf.length > 16 * 1024 * 1024) { writeJson(res, 400, { ok: false, error: '图片过大' }); return }
        mkdirSync(AVATAR_DIR, { recursive: true })
        for (const old of ['png', 'jpg', 'jpeg', 'webp']) {
          const f = join(AVATAR_DIR, `background.${old}`)
          try { if (existsSync(f)) unlinkSync(f) } catch { /* ignore */ }
        }
        writeFileSync(join(AVATAR_DIR, `background.${ext}`), buf)
        writeJson(res, 200, { ok: true })
      } catch (err) {
        writeJson(res, 500, { ok: false, error: (err && err.message) || '保存失败' })
      }
      return
    }

    if (pathname === '/api/wechat/background/delete') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      for (const ext of ['png', 'jpg', 'jpeg', 'webp']) {
        const f = join(AVATAR_DIR, `background.${ext}`)
        try { if (existsSync(f)) unlinkSync(f) } catch { /* ignore */ }
      }
      writeJson(res, 200, { ok: true })
      return
    }

    if (pathname === '/api/wechat/voice/send') {
      if (req.method !== 'POST') { writeJson(res, 405, { ok: false, code: 'method-not-allowed' }); return }
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      if (!device || !key) { writeJson(res, 400, { ok: false, code: 'missing-device', error: '缺少设备凭据' }); return }
      const type = String(req.headers['content-type'] || '').split(';', 1)[0].trim().toLowerCase()
      if (type !== 'audio/wav' && type !== 'audio/x-wav') { writeJson(res, 415, { ok: false, code: 'unsupported-format', error: '仅支持 PCM WAV 录音' }); return }
      const declared = Number(req.headers['content-length'])
      if (Number.isFinite(declared) && declared > 2_100_000) { writeJson(res, 413, { ok: false, code: 'too-large', error: '录音不能超过 60 秒' }); return }
      if (voiceUploadActive) { writeJson(res, 429, { ok: false, code: 'upload-busy', error: '正在处理上一段录音，请稍后重试' }); return }
      voiceUploadActive = true
      let job
      try {
        const body = await readRawBody(req, 2_100_000)
        if (!body.ok) {
          const status = body.tooLarge ? 413 : body.timeout ? 408 : 400
          writeJson(res, status, { ok: false, code: body.tooLarge ? 'too-large' : body.timeout ? 'upload-timeout' : 'bad-upload', error: '录音上传失败' })
          return
        }
        let wav
        try {
          wav = inspectPcmWav(body.buffer)
        } catch (err) {
          writeJson(res, 400, { ok: false, code: 'bad-audio', error: (err && err.message) || '录音格式无效' })
          return
        }
        const id = voice.add({ sessionKey: key, wav: body.buffer })
        voice.update(id, { durationMs: wav.durationMs })
        job = { id, wav: body.buffer, deviceId: device, sessionKey: key }
        // 先让手机立刻拿到语音条 id 用于渲染，再后台串行识别并自动送入电脑会话。
        writeJson(res, 200, { ok: true, id, durationMs: wav.durationMs, status: 'pending' })
      } finally {
        voiceUploadActive = false
      }
      if (job !== undefined) enqueueVoice(job)
      return
    }

    if (pathname.startsWith('/api/wechat/voice/') && req.method === 'GET') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      const id = pathname.slice('/api/wechat/voice/'.length)
      const file = voice.filePath(id)
      if (file === undefined || !existsSync(file)) { writeJson(res, 404, { ok: false, code: 'not-found' }); return }
      try {
        const data = readFileSync(file)
        res.writeHead(200, {
          'content-type': 'audio/wav',
          'content-length': String(data.length),
          'cache-control': 'private, max-age=86400',
        })
        res.end(data)
      } catch {
        writeJson(res, 500, { ok: false, code: 'read-failed' })
      }
      return
    }

    // 语音条状态：手机轮询它来更新「识别中 / 已发送 / 失败」与转写文字。
    if (pathname === '/api/wechat/voices') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      if (!key) { writeJson(res, 400, { ok: false, code: 'missing-device' }); return }
      writeJson(res, 200, { ok: true, voice: voice.list(key) })
      return
    }

    if (pathname === '/api/wechat/hide') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      if (!key) { writeJson(res, 400, { ok: false, error: '缺少设备凭据' }); return }
      const body = await readJsonBody(req, MAX_BODY)
      const ids = body && Array.isArray(body.ids) ? body.ids.filter(x => typeof x === 'string' && x !== '') : []
      if (ids.length === 0) { writeJson(res, 400, { ok: false, error: '没有要删除的消息' }); return }
      const hidden = loadHidden()
      const set = new Set(Array.isArray(hidden[key]) ? hidden[key] : [])
      for (const id of ids) set.add(id)
      hidden[key] = [...set].slice(-2000)
      persistHidden(hidden)
      writeJson(res, 200, { ok: true, hiddenIds: hidden[key] })
      return
    }

    if (pathname === '/api/wechat/nickname') {
      if (!gateOk(req, queryDevice)) { writeJson(res, 403, { ok: false, code: 'unpaired' }); return }
      if (!key) { writeJson(res, 400, { ok: false, error: '缺少设备凭据' }); return }
      const body = await readJsonBody(req, MAX_BODY)
      const name = body && typeof body.nickname === 'string' ? body.nickname.trim().slice(0, 30) : ''
      if (name === '') { writeJson(res, 400, { ok: false, error: '昵称不能为空' }); return }
      persistNickname(name)
      writeJson(res, 200, { ok: true, nickname: name })
      return
    }

    writeJson(res, 404, { ok: false, code: 'not-found' })
  }

  /* ── 固定公网地址：把安全网关挂到 Tailscale Funnel ───────────────── */

  const funnelState = { enabled: false, error: undefined }
  let funnelTimer = undefined

  function tailscaleCommand() {
    const explicit = typeof process.env.TAILSCALE_BIN === 'string' && process.env.TAILSCALE_BIN !== ''
      ? process.env.TAILSCALE_BIN
      : undefined
    if (explicit !== undefined && existsSync(explicit)) return explicit
    if (process.platform === 'win32') {
      const base = process.env['ProgramFiles'] || 'C:\\Program Files'
      const candidate = join(base, 'Tailscale', 'tailscale.exe')
      if (existsSync(candidate)) return candidate
    }
    return 'tailscale'
  }

  function runTailscale(args, timeoutMs) {
    return new Promise((resolve, reject) => {
      let child
      try {
        child = spawnProcess(tailscaleCommand(), args, { windowsHide: true })
      } catch (err) {
        reject(err)
        return
      }
      const output=createCliOutput();let errorText=''
      child.stdout.on('data', chunk=>{
        try{output.add(chunk)}catch(err){try{child.kill()}catch{};reject(err)}
      })
      child.stderr.on('data',chunk=>{errorText=(errorText+String(chunk)).slice(-8000)})
      const timer = setTimeout(() => { try { child.kill() } catch { /* gone */ } }, timeoutMs)
      timer.unref?.()
      child.on('error', (err) => { clearTimeout(timer); reject(err) })
      child.on('exit', (code) => {
        clearTimeout(timer)
        if (code === 0) { resolve(output.value()); return }
        const tail = (errorText||output.value()).trim().split('\n').filter(Boolean).slice(-1)[0]
        reject(new Error(tail || `tailscale exited ${String(code)}`))
      })
    })
  }

  let publicDnsTimer = undefined

  let publicProbeRunning=false;
  function watchPublicDns(port) {
    if (publicDnsTimer !== undefined) return;
    const check=async()=>{
      if(publicProbeRunning)return;
      const host=String(publicBaseUrl||'').replace(/^https:\/\//,'').replace(/\/.*$/,'');
      if(!host)return;
      publicProbeRunning=true;
      try {
        const value=await probePublicIngress(host);
        funnelState.publicReachable=value.reachable;
        funnelState.publicState=value.state;
        funnelState.checkedAt=Date.now()/1000;
        if(value.state==='dns_missing')await applyFunnel(port);
        if(value.reachable===false)funnelState.error=value.state==='dns_missing'?'公网DNS缺失，已尝试重新发布':'公网TLS入口断开；本地在线不等于公网可用，请检查Tailscale服务';
        else if(value.reachable===true)funnelState.error=undefined;
        const path=join(home,'public-health.json'),temp=path+'.tmp';
        writeFileSync(temp,JSON.stringify({...value,checked_at:funnelState.checkedAt}));renameSync(temp,path);
      } finally {publicProbeRunning=false;}
    };
    publicDnsTimer=setInterval(()=>{void check().catch(()=>{});},60000);
    publicDnsTimer.unref?.();
    void check().catch(()=>{});
  }

  async function applyFunnel(port) {
    try {
      const status = JSON.parse(await runTailscale(['status', '--json'], 15_000))
      if(status.BackendState!=='Running')throw new Error(status.BackendState==='NeedsLogin'?'Tailscale未登录，请在电脑Tailscale中登录':'Tailscale服务未就绪：'+String(status.BackendState||'unknown'))
      await runTailscale(funnelArgs(port), 60_000)
      funnelState.enabled = true
      funnelState.error = undefined
      console.log(`wechat-chat: Tailscale Funnel 已指向 127.0.0.1:${String(port)}`)
      if (publicBaseUrl === undefined) {
        try {
          const host = String((status && status.Self && status.Self.DNSName) || '').replace(/\.$/, '')
          if (host !== '') publicBaseUrl = `https://${host}`
        } catch { /* 保留未设置，回退到局域网地址 */ }
      }
      watchPublicDns(port)
    } catch (err) {
      funnelState.enabled = false
      funnelState.error = (err && err.message) || String(err)
      console.error(`wechat-chat: Tailscale Funnel 启动失败：${funnelState.error}`)
      clearTimeout(funnelTimer)
      funnelTimer = setTimeout(() => { void applyFunnel(port) }, 15_000)
      funnelTimer.unref?.()
    }
  }

  host.effect(() => {
    const disposers = [
      host.webServer.register({ kind: 'exact', path: '/wechat', handler: handlePage }),
      host.webServer.register({ kind: 'exact', path: '/whale-panel', handler: handlePanel }),
      host.webServer.register({ kind: 'prefix', path: '/api/whale', handler: handleWhaleApi }),
      host.webServer.register({ kind: 'prefix', path: '/api/wechat', handler: handleWechatApi }),
    ]
    const timer = setInterval(() => { service.sweep() }, 10_000)
    timer.unref()
    let active = true
    if (autoTunnel || funnelEnabled) {
      void authGateway.start().then((gatewayTarget) => {
        if (!active) { authGateway.stop(); return }
        if (funnelEnabled) {
          const gatewayPort = Number(new URL(gatewayTarget).port)
          console.log(`wechat-chat: 公网地址走 Tailscale Funnel（固定域名），网关端口 ${String(gatewayPort)}`)
          void applyFunnel(gatewayPort)
          return
        }
        tunnel.target = gatewayTarget
        tunnel.start()
        console.log(`wechat-chat: 正在启动${tunnelPasswordHash !== '' ? '带密码保护的' : '受限路径的'}免费公网隧道（Cloudflare quick tunnel）…`)
      }).catch((err) => {
        console.error(`wechat-chat: 无法启动公网安全网关：${(err && err.message) || String(err)}`)
      })
    }
    return () => {
      active = false
      clearTimeout(funnelTimer)
      for (const dispose of disposers) dispose()
      clearInterval(timer)
      tunnel.dispose()
      authGateway.stop()
      stt.dispose()
    }
  }, 'wechat-chat: routes')

  if (lanIPv4Addresses().length > 0 && Number.isFinite(host.webServer.port)) {
    console.log(`wechat-chat: 鲸聊配对面板 http://127.0.0.1:${String(host.webServer.port)}/whale-panel`)
  }
}

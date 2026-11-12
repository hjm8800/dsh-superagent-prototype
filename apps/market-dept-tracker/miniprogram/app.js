/**
 * The mini program.
 *
 * The platform runs as a small server; this asks it for the data it shows. A
 * person opens the mini program once, enters the link their department gave
 * them, and from then on sees their own task list.
 */

const config = require('./config.js')

/** Where the platform answers; change it in config.js, not here. */
const BASE_URL = config.baseUrl

App({
  globalData: {
    baseUrl: BASE_URL,
    token: '',
    person: null,
    position: null,
    role: 'member',
  },

  onLaunch() {
    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
      this.whoAmI(token).catch(() => {
        // The link may have been withdrawn; ask for it again.
        wx.removeStorageSync('token')
        this.globalData.token = ''
      })
    }
  },

  /** Ask the platform who this link belongs to. */
  async whoAmI(token) {
    const data = await this.request('/api/me', { token })
    this.globalData.person = data.person
    this.globalData.position = data.position
    this.globalData.role = data.role
    return data
  },

  /** Remember the post account, and check it. */
  async signIn(username, password) {
    const name = String(username || '').trim()
    const secret = String(password || '')
    if (!name) throw new Error('请输入岗位账户')
    if (!secret) throw new Error('请输入密码')
    const data = await this.post('/api/login', { username: name, password: secret })
    this.globalData.token = data.token
    wx.setStorageSync('token', data.token)
    await this.whoAmI(data.token)
    return data
  },

  signOut() {
    wx.removeStorageSync('token')
    this.globalData.token = ''
    this.globalData.person = null
    this.globalData.position = null
    this.globalData.role = 'member'
  },

  /** One POST to the platform. */
  post(path, fields) {
    const body = Object.keys(fields || {})
      .map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(fields[key])}`)
      .join('&')
    return new Promise((resolve, reject) => {
      wx.request({
        url: `${BASE_URL}${path}`,
        method: 'POST',
        header: { 'content-type': 'application/x-www-form-urlencoded' },
        data: body,
        timeout: 15000,
        success(response) {
          const data = response.data
          if (response.statusCode === 200 && data && data.ok) resolve(data)
          else reject(new Error((data && data.error) || '账户或密码不对'))
        },
        fail(error) {
          reject(new Error(error.errMsg || '连不上服务器'))
        },
      })
    })
  },

  /** One call to the platform. */
  request(path, query) {
    const parts = []
    for (const key of Object.keys(query || {})) {
      const value = query[key]
      if (value !== undefined && value !== null && value !== '') {
        parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
      }
    }
    const url = `${BASE_URL}${path}${parts.length ? `?${parts.join('&')}` : ''}`
    return new Promise((resolve, reject) => {
      wx.request({
        url,
        method: 'GET',
        timeout: 15000,
        success(response) {
          const data = response.data
          if (response.statusCode === 200 && data && data.ok) resolve(data)
          else reject(new Error((data && data.error) || `请求失败（${response.statusCode}）`))
        },
        fail(error) {
          reject(new Error(error.errMsg || '连不上服务器'))
        },
      })
    })
  },
})

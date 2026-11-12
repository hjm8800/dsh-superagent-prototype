const app = getApp()

/** How often a fixed task repeats. */
const CADENCES = [
  { value: 'week', label: '每周' },
  { value: 'month', label: '每月' },
]

/**
 * A leader sets up a fixed task for a post.
 *
 * The task then appears in every period of that cadence for the people
 * holding the post; it is not one more thing handed out by hand.
 */
Page({
  data: {
    loading: true,
    error: '',
    forbidden: false,
    positions: [],
    positionIndex: 0,
    cadences: CADENCES,
    cadenceIndex: 0,
    title: '',
    detail: '',
    submitting: false,
    lastResult: '',
  },

  onShow() {
    this.load()
  },

  async load() {
    const token = app.globalData.token || wx.getStorageSync('token')
    if (!token) {
      this.setData({ loading: false, forbidden: true, error: '' })
      return
    }
    this.setData({ loading: true, error: '', forbidden: false })
    try {
      const data = await app.request('/api/assignable', { token })
      const index = Math.min(this.data.positionIndex, Math.max(0, data.positions.length - 1))
      this.setData({ loading: false, positions: data.positions, positionIndex: index })
    } catch (error) {
      const message = error.message || '加载失败'
      this.setData({ loading: false, error: message, forbidden: message.indexOf('没有可以下达任务的岗位') >= 0 })
    }
  },

  pickPosition(event) {
    this.setData({ positionIndex: Number(event.detail.value) })
  },

  pickCadence(event) {
    this.setData({ cadenceIndex: Number(event.detail.value) })
  },

  inputTitle(event) {
    this.setData({ title: event.detail.value })
  },

  inputDetail(event) {
    this.setData({ detail: event.detail.value })
  },

  async submit() {
    if (this.data.submitting) return
    const post = this.data.positions[this.data.positionIndex]
    if (!post) {
      wx.showToast({ title: '请先选岗位', icon: 'none' })
      return
    }
    if (!this.data.title.trim()) {
      wx.showToast({ title: '请填任务名称', icon: 'none' })
      return
    }
    const cadence = CADENCES[this.data.cadenceIndex]
    const token = app.globalData.token
    this.setData({ submitting: true })
    try {
      const data = await new Promise((resolve, reject) => {
        wx.request({
          url: `${app.globalData.baseUrl}/api/tasks/recurring`,
          method: 'POST',
          header: { 'content-type': 'application/x-www-form-urlencoded' },
          data: [
            `token=${encodeURIComponent(token)}`,
            `positionId=${encodeURIComponent(post.id)}`,
            `cadence=${encodeURIComponent(cadence.value)}`,
            `title=${encodeURIComponent(this.data.title)}`,
            `detail=${encodeURIComponent(this.data.detail)}`,
          ].join('&'),
          success(r) {
            if (r.statusCode === 200 && r.data && r.data.ok) resolve(r.data)
            else reject(new Error((r.data && r.data.error) || '设置失败'))
          },
          fail: (e) => reject(new Error(e.errMsg || '连不上服务器')),
        })
      })
      wx.showToast({ title: '已设置', icon: 'success' })
      this.setData({
        title: '',
        detail: '',
        lastResult: `已为「${data.position}」设${cadence.label}固定任务`,
      })
    } catch (error) {
      wx.showToast({ title: error.message || '设置失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  },

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
})

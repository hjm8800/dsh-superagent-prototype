const app = getApp()

Page({
  data: {
    loading: true,
    error: '',
    forbidden: false,
    positions: [],
    positionIndex: 0,
    periodType: 'week',
    periodKey: '',
    periodLabel: '',
    periods: [],
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
      const periods = await app.request('/api/periods', { type: this.data.periodType })
      const current = periods.periods[0] || { key: '', label: '' }
      this.setData({
        loading: false,
        positions: data.positions,
        periodKey: this.data.periodKey || current.key,
        periodLabel: current.label,
        periods: periods.periods,
      })
    } catch (error) {
      const message = error.message || '加载失败'
      this.setData({ loading: false, error: message, forbidden: message.indexOf('没有可以下达任务的岗位') >= 0 })
    }
  },

  pickPosition(event) {
    this.setData({ positionIndex: Number(event.detail.value) })
  },

  pickPeriod(event) {
    const index = Number(event.detail.value)
    const period = this.data.periods[index]
    this.setData({ periodKey: period.key, periodLabel: period.label })
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
    const token = app.globalData.token
    this.setData({ submitting: true })
    try {
      const data = await new Promise((resolve, reject) => {
        wx.request({
          url: `${app.globalData.baseUrl}/api/tasks/assign`,
          method: 'POST',
          header: { 'content-type': 'application/x-www-form-urlencoded' },
          data: [
            `token=${encodeURIComponent(token)}`,
            `positionId=${encodeURIComponent(post.id)}`,
            `periodType=${encodeURIComponent(this.data.periodType)}`,
            `periodKey=${encodeURIComponent(this.data.periodKey)}`,
            `title=${encodeURIComponent(this.data.title)}`,
            `detail=${encodeURIComponent(this.data.detail)}`,
          ].join('&'),
          success(r) {
            if (r.statusCode === 200 && r.data && r.data.ok) resolve(r.data)
            else reject(new Error((r.data && r.data.error) || '下达失败'))
          },
          fail: (e) => reject(new Error(e.errMsg || '连不上服务器')),
        })
      })
      wx.showToast({ title: '已下达', icon: 'success' })
      this.setData({ title: '', detail: '', lastResult: `已下达给「${data.position}」的 ${data.people} 人` })
    } catch (error) {
      wx.showToast({ title: error.message || '下达失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  },
})

const app = getApp()

Page({
  data: {
    loading: true,
    error: '',
    forbidden: false,
    periodLabel: '',
    periodType: 'week',
    periods: [],
    stats: { peopleCount: 0, reportedCount: 0, unmatchedFiles: 0, taskDone: 0, taskTotal: 0 },
    positions: [],
  },

  onShow() {
    this.load()
  },

  async load(periodKey) {
    const token = app.globalData.token || wx.getStorageSync('token')
    if (!token) {
      this.setData({ loading: false, forbidden: true, error: '' })
      return
    }
    this.setData({ loading: true, error: '', forbidden: false })
    try {
      const data = await app.request('/api/overview', {
        token,
        type: this.data.periodType,
        period: periodKey || '',
      })
      const periods = await app.request('/api/periods', { type: this.data.periodType })
      this.setData({
        loading: false,
        periodLabel: data.periodLabel,
        periods: periods.periods,
        stats: {
          peopleCount: data.peopleCount,
          reportedCount: data.reportedCount,
          unmatchedFiles: data.unmatchedFiles,
          taskDone: data.tasks.done,
          taskTotal: data.tasks.total,
        },
        positions: data.positions,
      })
    } catch (error) {
      const message = error.message || '加载失败'
      this.setData({
        loading: false,
        error: message,
        forbidden: message.indexOf('领导') >= 0,
      })
    }
  },

  pickPeriod(event) {
    this.load(event.currentTarget.dataset.key)
  },

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  goAssign() {
    wx.navigateTo({ url: '/pages/assign/assign' })
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
})

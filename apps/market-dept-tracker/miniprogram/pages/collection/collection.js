const app = getApp()

const PERIOD_TYPES = [
  { value: 'week', label: '周' },
  { value: 'month', label: '月' },
]

/**
 * What the collector made of the outcome files.
 *
 * Only the leaders see this. Files it could not place are listed with the
 * reason, so nothing is silently dropped.
 */
Page({
  data: {
    loading: true,
    error: '',
    forbidden: false,
    periodTypes: PERIOD_TYPES,
    periodType: 'week',
    periodKey: '',
    periodLabel: '',
    periods: [],
    counts: { files: 0, recognized: 0, unmatched: 0 },
    collectedAt: '',
    positions: [],
    files: [],
    unmatched: [],
    showAll: false,
  },

  onShow() {
    this.load()
  },

  async load(periodKey, type) {
    const token = app.globalData.token || wx.getStorageSync('token')
    if (!token) {
      this.setData({ loading: false, forbidden: true, error: '' })
      return
    }
    const periodType = type || this.data.periodType
    this.setData({ loading: true, error: '', forbidden: false })
    try {
      const data = await app.request('/api/collection', {
        token,
        type: periodType,
        period: periodKey || '',
      })
      const periods = await app.request('/api/periods', { type: periodType })
      this.setData({
        loading: false,
        periodType: data.periodType,
        periodKey: data.periodKey,
        periodLabel: data.periodLabel,
        periods: periods.periods,
        counts: data.counts,
        collectedAt: data.collectedAt,
        positions: data.positions,
        files: data.files,
        unmatched: data.unmatched,
      })
    } catch (error) {
      const message = error.message || '加载失败'
      this.setData({ loading: false, error: message, forbidden: message.indexOf('领导') >= 0 })
    }
  },

  switchType(event) {
    const type = event.currentTarget.dataset.type
    if (!type || type === this.data.periodType) return
    this.setData({ periodType: type, periodKey: '' })
    this.load('', type)
  },

  pickPeriod(event) {
    this.load(event.currentTarget.dataset.key)
  },

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  onPullDownRefresh() {
    this.load(this.data.periodKey).finally(() => wx.stopPullDownRefresh())
  },
})

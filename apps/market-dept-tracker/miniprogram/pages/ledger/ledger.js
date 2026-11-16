const app = getApp()

/**
 * The department ledger, by post.
 *
 * The source table leaves a few posts open; the page says so instead of
 * quietly dropping them. Nobody is named here.
 */
Page({
  data: {
    loading: true,
    error: '',
    forbidden: false,
    departmentName: '',
    positions: [],
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
      const data = await app.request('/api/ledger', { token })
      this.setData({
        loading: false,
        departmentName: data.departmentName,
        positions: data.positions,
      })
    } catch (error) {
      const message = error.message || '加载失败'
      this.setData({ loading: false, error: message, forbidden: message.indexOf('领导') >= 0 })
    }
  },

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },
})

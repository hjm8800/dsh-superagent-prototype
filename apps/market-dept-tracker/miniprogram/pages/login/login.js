const app = getApp()

Page({
  data: {
    username: '',
    password: '',
    busy: false,
    error: '',
  },

  onUsername(event) {
    this.setData({ username: event.detail.value, error: '' })
  },

  onPassword(event) {
    this.setData({ password: event.detail.value, error: '' })
  },

  async submit() {
    if (this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      await app.signIn(this.data.username, this.data.password)
      const target = app.globalData.role === 'leader' ? '/pages/overview/overview' : '/pages/tasks/tasks'
      wx.switchTab({ url: target })
    } catch (error) {
      this.setData({ error: error.message || '登录失败' })
    } finally {
      this.setData({ busy: false })
    }
  },
})

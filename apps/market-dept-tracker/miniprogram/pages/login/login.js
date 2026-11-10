const app = getApp()

Page({
  data: {
    link: '',
    busy: false,
    error: '',
  },

  onInput(event) {
    this.setData({ link: event.detail.value, error: '' })
  },

  async submit() {
    if (this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      await app.signIn(this.data.link)
      wx.switchTab({ url: '/pages/tasks/tasks' })
    } catch (error) {
      this.setData({ error: error.message || '链接无效' })
    } finally {
      this.setData({ busy: false })
    }
  },
})

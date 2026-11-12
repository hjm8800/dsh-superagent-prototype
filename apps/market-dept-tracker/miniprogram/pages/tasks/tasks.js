const app = getApp()

/** How the task statuses are offered, in the order a person works through them. */
const STATUS_CHOICES = [
  { value: 'open', label: '待办' },
  { value: 'in_progress', label: '进行中' },
  { value: 'blocked', label: '受阻' },
  { value: 'done', label: '已完成' },
]

Page({
  data: {
    loading: true,
    error: '',
    person: null,
    position: '',
    periodType: 'week',
    periodTypes: [
      { value: 'week', label: '周' },
      { value: 'month', label: '月' },
    ],
    periodLabel: '',
    periods: [],
    tasks: [],
    stats: { total: 0, done: 0, open: 0, blocked: 0 },
    canAssign: false,
    statusChoices: STATUS_CHOICES,
  },

  onShow() {
    this.load()
  },

  async load(periodKey, type) {
    const token = app.globalData.token || wx.getStorageSync('token')
    if (!token) {
      this.setData({ loading: false, tasks: [], error: '' })
      return
    }
    const periodType = type || this.data.periodType
    this.setData({ loading: true, error: '', periodType })
    try {
      if (!app.globalData.person) await app.whoAmI(token)
      const data = await app.request('/api/my-tasks', {
        token,
        type: periodType,
        period: periodKey || '',
      })
      const periods = await app.request('/api/periods', { type: periodType })
      let canAssign = false
      try {
        const assignable = await app.request('/api/assignable', { token })
        canAssign = (assignable.positions || []).length > 0
      } catch (error) {
        canAssign = false
      }
      const tasks = data.tasks.map((task) => ({
        ...task,
        statusIndex: Math.max(0, STATUS_CHOICES.findIndex((c) => c.value === task.status)),
        photos: (task.photos || []).map((photo) => ({
          id: photo.id,
          url: `${app.globalData.baseUrl}/photos/${photo.id}?token=${encodeURIComponent(token)}`,
        })),
      }))
      this.setData({
        loading: false,
        person: app.globalData.person,
        position: data.position,
        periodLabel: data.periodLabel,
        periods: periods.periods,
        tasks,
        canAssign,
        stats: {
          total: tasks.length,
          done: tasks.filter((t) => t.status === 'done').length,
          open: tasks.filter((t) => t.status === 'open').length,
          blocked: tasks.filter((t) => t.status === 'blocked').length,
        },
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '加载失败', tasks: [] })
    }
  },

  pickPeriod(event) {
    const key = event.currentTarget.dataset.key
    this.load(key)
  },

  switchType(event) {
    const type = event.currentTarget.dataset.type
    if (!type || type === this.data.periodType) return
    this.setData({ periodType: type })
    this.load('', type)
  },

  async changeStatus(event) {
    const taskId = event.currentTarget.dataset.id
    const choice = STATUS_CHOICES[Number(event.detail.value)]
    if (!choice) return
    const token = app.globalData.token
    try {
      await new Promise((resolve, reject) => {
        wx.request({
          url: `${app.globalData.baseUrl}/api/my-tasks/status`,
          method: 'POST',
          header: { 'content-type': 'application/x-www-form-urlencoded' },
          data: `token=${encodeURIComponent(token)}&id=${encodeURIComponent(taskId)}&status=${choice.value}`,
          success(r) {
            if (r.statusCode === 200 && r.data && r.data.ok) resolve(r.data)
            else reject(new Error((r.data && r.data.error) || '保存失败'))
          },
          fail: (e) => reject(new Error(e.errMsg || '连不上服务器')),
        })
      })
      wx.showToast({ title: '已更新', icon: 'success' })
      this.load(this.currentPeriodKey())
    } catch (error) {
      wx.showToast({ title: error.message || '保存失败', icon: 'none' })
    }
  },

  /** The period the list is showing. */
  currentPeriodKey() {
    const found = this.data.periods.find((p) => p.label === this.data.periodLabel)
    return found ? found.key : ''
  },

  /** Take or pick several photos and attach them to a task. */
  async choosePhotos(event) {
    const taskId = event.currentTarget.dataset.id
    const media = await new Promise((resolve) => {
      wx.chooseMedia({
        count: 9,
        mediaType: ['image'],
        sourceType: ['camera', 'album'],
        success: resolve,
        fail: () => resolve(null),
      })
    })
    if (!media || !media.tempFiles || media.tempFiles.length === 0) return
    wx.showLoading({ title: '上传中' })
    try {
      await app.uploadPhotos(
        taskId,
        media.tempFiles.map((file) => file.tempFilePath),
      )
      wx.hideLoading()
      wx.showToast({ title: '已上传', icon: 'success' })
      this.load(this.currentPeriodKey())
    } catch (error) {
      wx.hideLoading()
      wx.showToast({ title: error.message || '上传失败', icon: 'none' })
    }
  },

  /** Look at a task's photos full size. */
  previewPhoto(event) {
    const taskId = event.currentTarget.dataset.task
    const task = this.data.tasks.find((t) => t.id === taskId)
    if (!task || task.photos.length === 0) return
    wx.previewImage({ current: event.currentTarget.dataset.url, urls: task.photos.map((p) => p.url) })
  },

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  goAssign() {
    wx.navigateTo({ url: '/pages/assign/assign' })
  },

  goRecurring() {
    wx.navigateTo({ url: '/pages/recurring/recurring' })
  },

  onPullDownRefresh() {
    this.load(this.currentPeriodKey()).finally(() => wx.stopPullDownRefresh())
  },

  signOut() {
    app.signOut()
    this.setData({ person: null, tasks: [], error: '' })
  },
})

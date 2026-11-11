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
      const tasks = data.tasks.map((task) => ({
        ...task,
        statusIndex: Math.max(0, STATUS_CHOICES.findIndex((c) => c.value === task.status)),
      }))
      this.setData({
        loading: false,
        person: app.globalData.person,
        position: data.position,
        periodLabel: data.periodLabel,
        periods: periods.periods,
        tasks,
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

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  onPullDownRefresh() {
    this.load(this.currentPeriodKey()).finally(() => wx.stopPullDownRefresh())
  },

  signOut() {
    app.signOut()
    this.setData({ person: null, tasks: [], error: '' })
  },
})

const app = getApp()

/** The statuses a duty may be reported in. */
const STATUS_CHOICES = [
  { value: 'done', label: '已完成' },
  { value: 'in_progress', label: '进行中' },
  { value: 'not_started', label: '未开始' },
  { value: 'blocked', label: '受阻' },
  { value: 'not_applicable', label: '不适用' },
]

Page({
  data: {
    loading: true,
    error: '',
    position: '',
    instructions: '',
    boundary: '',
    periodType: 'week',
    periodTypes: [
      { value: 'week', label: '周' },
      { value: 'month', label: '月' },
    ],
    periodKey: '',
    periodLabel: '',
    periods: [],
    duties: [],
    statusChoices: STATUS_CHOICES,
    summary: '',
    blockers: '',
    nextPlan: '',
    submitting: false,
    submittedAt: '',
  },

  onShow() {
    this.load()
  },

  async load(periodKey, type) {
    const token = app.globalData.token || wx.getStorageSync('token')
    if (!token) {
      this.setData({ loading: false, error: '请先在「我的任务」页粘贴链接进入' })
      return
    }
    const periodType = type || this.data.periodType
    this.setData({ loading: true, error: '', periodType })
    try {
      const data = await app.request('/api/my-duties', {
        token,
        type: periodType,
        period: periodKey || '',
      })
      const periods = await app.request('/api/periods', { type: periodType })
      this.setData({
        loading: false,
        position: data.position,
        instructions: data.instructions,
        boundary: data.boundary,
        periodKey: data.periodKey,
        periodLabel: data.periodLabel,
        periods: periods.periods,
        duties: data.duties.map((duty) => ({
          ...duty,
          statusIndex: Math.max(0, STATUS_CHOICES.findIndex((c) => c.value === duty.status)),
          percent: duty.percent === null || duty.percent === undefined ? '' : String(duty.percent),
          note: duty.note || '',
        })),
        summary: data.dismissed ? data.dismissed.summary : '',
        blockers: data.dismissed ? data.dismissed.blockers : '',
        nextPlan: data.dismissed ? data.dismissed.nextPlan : '',
        submittedAt: data.dismissed ? data.dismissed.submittedAt : '',
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '加载失败' })
    }
  },

  pickPeriod(event) {
    this.load(event.currentTarget.dataset.key)
  },

  switchType(event) {
    const type = event.currentTarget.dataset.type
    if (!type || type === this.data.periodType) return
    this.setData({ periodType: type })
    this.load('', type)
  },

  changeStatus(event) {
    const index = Number(event.currentTarget.dataset.index)
    const dutyIndex = Number(event.currentTarget.dataset.duty)
    const duties = this.data.duties.slice()
    duties[dutyIndex].statusIndex = index
    duties[dutyIndex].status = STATUS_CHOICES[index].value
    this.setData({ duties })
  },

  inputPercent(event) {
    const dutyIndex = Number(event.currentTarget.dataset.duty)
    const duties = this.data.duties.slice()
    duties[dutyIndex].percent = event.detail.value
    this.setData({ duties })
  },

  inputNote(event) {
    const dutyIndex = Number(event.currentTarget.dataset.duty)
    const duties = this.data.duties.slice()
    duties[dutyIndex].note = event.detail.value
    this.setData({ duties })
  },

  inputSummary(event) {
    this.setData({ summary: event.detail.value })
  },

  inputBlockers(event) {
    this.setData({ blockers: event.detail.value })
  },

  inputNextPlan(event) {
    this.setData({ nextPlan: event.detail.value })
  },

  async submit() {
    if (this.data.submitting) return
    const token = app.globalData.token
    this.setData({ submitting: true })
    try {
      const items = this.data.duties.map((duty) => ({
        id: duty.id,
        status: STATUS_CHOICES[duty.statusIndex].value,
        percent:
          duty.percent === '' || duty.percent === null || duty.percent === undefined
            ? null
            : Number(duty.percent),
        note: duty.note || '',
      }))
      await new Promise((resolve, reject) => {
        wx.request({
          url: `${app.globalData.baseUrl}/api/report`,
          method: 'POST',
          header: { 'content-type': 'application/x-www-form-urlencoded' },
          data: [
            `token=${encodeURIComponent(token)}`,
            `periodType=${encodeURIComponent(this.data.periodType)}`,
            `periodKey=${encodeURIComponent(this.data.periodKey)}`,
            `summary=${encodeURIComponent(this.data.summary)}`,
            `blockers=${encodeURIComponent(this.data.blockers)}`,
            `nextPlan=${encodeURIComponent(this.data.nextPlan)}`,
            `items=${encodeURIComponent(JSON.stringify(items))}`,
          ].join('&'),
          success(r) {
            if (r.statusCode === 200 && r.data && r.data.ok) resolve(r.data)
            else reject(new Error((r.data && r.data.error) || '提交失败'))
          },
          fail: (e) => reject(new Error(e.errMsg || '连不上服务器')),
        })
      })
      wx.showToast({ title: '已提交', icon: 'success' })
      this.load(this.data.periodKey)
    } catch (error) {
      wx.showToast({ title: error.message || '提交失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  },

  onPullDownRefresh() {
    this.load(this.data.periodKey).finally(() => wx.stopPullDownRefresh())
  },
})

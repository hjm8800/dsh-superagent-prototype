/**
 * Where the platform answers.
 *
 * 平台已经部署在 https://morebrighter.cn，下面直接用它。
 *
 * 在微信开发者工具里调试时，如果还没把域名加进 request 合法域名，
 * 可以勾上「详情 → 本地设置 → 不校验合法域名、web-view（业务域名）、
 * TLS 版本以及 HTTPS 证书」先跑。
 *
 * 发布前要在微信后台「开发 → 开发管理 → 服务器域名 → request 合法域名」
 * 加上 https://morebrighter.cn，并要求该域名已完成 ICP 备案。
 */
module.exports = {
  baseUrl: 'https://morebrighter.cn',
}

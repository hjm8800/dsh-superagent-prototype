/**
 * Where the platform answers.
 *
 * 市场部工作台固定走 8443（同一台服务器上的 80/443 属于另一个工作台，不要占用）。
 * 端口用自签证书，浏览器打开时要先点“继续”；微信开发者工具里勾上
 * 「详情 → 本地设置 → 不校验合法域名、web-view（业务域名）、TLS 版本以及
 * HTTPS 证书」即可直接连。
 *
 * 发布前要在微信后台「开发 → 开发管理 → 服务器域名 → request 合法域名」
 * 加上一个已完成 ICP 备案的 https 域名（例如 https://morebrighter.cn），
 * 并把下面的地址换成它。
 */
module.exports = {
  baseUrl: 'https://43.138.176.177:8443',
}

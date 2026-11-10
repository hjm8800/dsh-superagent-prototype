/**
 * Where the platform answers.
 *
 * 先在开发机上试：微信开发者工具里勾上
 *   「详情 → 本地设置 → 不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」
 * 这样就能连局域网里的平台，不用先买域名。
 *
 * 要发布（给全部门用）时，把下面换成 https 的域名，例如
 *   baseUrl: 'https://mkt.example.com'
 * 并在微信后台「开发 → 开发管理 → 服务器域名 → request 合法域名」加上同一个域名。
 */
module.exports = {
  baseUrl: 'http://192.168.10.131:3000',
}

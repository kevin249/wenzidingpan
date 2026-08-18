'use strict';

/** 新浪和腾讯的行情接口都返回 GBK 文本，统一在这里解码。 */
function decodeGbk(buffer) {
  for (const enc of ['gbk', 'gb18030']) {
    try {
      return new TextDecoder(enc).decode(buffer);
    } catch {
      /* 运行时 ICU 不含该编码时继续尝试下一个 */
    }
  }
  // 兜底：股票名称可能乱码，但价格字段是 ASCII，行情本身照常可用。
  return Buffer.from(buffer).toString('latin1');
}

module.exports = { decodeGbk };

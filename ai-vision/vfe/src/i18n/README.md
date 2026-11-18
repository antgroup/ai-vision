# 平台前端国际化

平台支持 `zh-CN`、`en-US`、`ja-JP`。默认中文，选择保存在浏览器的 `localStorage.language`。认证页和用户菜单共用 `languageOptions`、`changeLanguage()`，`getCurrentLocale()` 返回规范化的支持语言。

## 文案维护

- 日文覆盖现有英文资源。新增产品文案时同步维护三套资源，包含动态键、插值、富文本和无障碍标签。
- 同时参考中英文及真实页面，明确对象、状态和交互用途后再写日文；英文是覆盖范围参照，不是唯一语义来源。
- 保留变量名、富文本标签、命令、代码和品牌。运行信号、风险线索、评分不能译成已确认故障、违规或正确率。
- 常用术语：Space → スペース，Agent → エージェント，Trace → トレース，Session → セッション，Diagnoser → 診断器，Trial → 試行実行。
- 不翻译原始模型输出、用户内容及自定义名称。只有显式本地化的内置标签和服务端枚举使用前端译文。
- 材料名 `policy:language` 中的冒号是键的一部分；该调用使用 `nsSeparator: false`，不改变其他调用的 namespace 规则。

日文资源缺失时依次回退英文、中文；英文仍回退中文。回退只作兜底，不能代替完整翻译。

## 日历与格式

`calendar.ts` 为两处时间选择器提供 locale 映射。日文使用 DayPicker 对 date-fns 日文 locale 的扩展，同时提供月份、星期和无障碍导航提示；中文和英文保持原有配置。

首期日文数字缩写使用 K/M/B，通用时长使用 ms/s/min/h/d。不改变时区及时间筛选语义；既有固定日期格式、浏览器默认日期和固定中文日期暂未全量统一。

初始化和切换时同步文档根节点的 `lang`。`index.html` 的 `translate="no"` / `notranslate` 防护应继续保留，避免浏览器自动翻译改写 React 节点。

## 检查

在 `ai-vision/vfe/` 执行：

```bash
npm run test:i18n
npm run test:builtin-rule-labels
npm run test:browser-dom-protection
npm run typecheck
npm run build
```

`test:i18n` 使用真实 i18next 验证资源覆盖、数组、重复键、变量与标签、持久化、回退、日历、数字格式和内置标签语义。译文自然度和页面布局还需要结合实际页面检查。

## 当前覆盖边界

日文与现有英文覆盖对齐。Session / Trace 详情中的部分硬编码文案、执行历史中的部分标题及日期、后端原始错误消息等既有遗漏仍会显示原语言。本次没有对这些范围外内容做全量改造。

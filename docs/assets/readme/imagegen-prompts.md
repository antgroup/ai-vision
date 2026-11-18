# ImageGen prompt set

The README uses three focused capability illustrations, each localized into Chinese, English, and Japanese. These illustrations use built-in ImageGen and depict conceptual interfaces, not screenshots or customer measurements. They contain no product logos. Actual onboarding screenshots are documented separately in the [asset guide](README.md). Chinese-UI promotion is confined to the Chinese README. Preserve current feature boundaries and inspect every localized output.

## observe.zh-CN

```text
Use case: infographic-diagram. Create a finished, exceptionally polished product-concept illustration for a professional open-source Agent observability README. Landscape 4:3 canvas, highest practical native resolution, crisp readable text. Sophisticated dark navy #0c1220 editorial design, warm white typography, lavender #bba7ff, mint #94e6c0 and restrained amber for anomalies. Precise interface mockups, layered inspector panels, intentional asymmetrical hierarchy, fine rules, generous inner padding, sharp orthogonal connectors. Make the interfaces the visual evidence rather than decorations. NO product logos, no invented symbols or wordmarks, no circular brand marks, no AI Vision lettering, no vendor icons, no photographs, robots, floating 3D, slogans not supplied, fake customer claims or invented percentages. Generic functional icons are allowed. Text-only technical names are allowed. Do not claim a Chinese UI or Chinese support. This is a CONCEPT illustration, not a screenshot; visibly label it. Use ONLY the supplied copy and necessary short technical UI labels. Do not add filler paragraphs or tiny illegible annotations. All labels must fit.
Topic 01: collection coverage and useful usage analysis, not diagnosis or execution views.
Exact large heading: "正在用的 Agent，都值得被看见"
Subtitle: "应用与框架接入 → 运行与用量看板 → 回查具体任务".
Upper left, two distinct short input lanes with understated category headings: "Agent 应用" and below "Codex · Claude Code · Kimi CLI" / "OpenCode · OpenClaw · …"; secondary lane "开发框架" and "LangChain · LangGraph" / "Deep Agents · Google ADK · …". Arrow from applications labelled "采集插件"; arrow from frameworks labelled "Langfuse SDK / NeMo Relay". These join a shared trace stream, then feed the main dashboard. No logos or decorative orbit networks.
Dominant lower two-thirds: a finely designed dashboard with top filter pills "空间" "Agent" "时间范围", compact measure headings "模型请求" "工具调用" "Token" "可得费用". Use sparklines and bars WITHOUT numerical measured totals. A two-column panel: wide left "用量分布" with 3 text-only rows "应用 / 框架" "模型" "工具"; mint/lavender stacked bars conceptually compare categories. Narrow right "值得回查的任务" with three trace rows "重试较多" (amber selected), "耗时较长", "工具失败". The selected row connects to a small zoomed evidence card "Trace → 相关步骤", label "把消耗放回任务". Make the selective drilldown obvious.
A slim, clearly separate useful note: "已有 Langfuse 埋点：切换地址与凭据，复用支持的采集路径".
Footer: "概念示意 · 字段随采集来源而异，未知用量不等于零".
Composition must be clean enough to explain ONLY seeing coverage and usage. No three-pillar overview, no branding.
```

## understand.zh-CN

```text
Use case: infographic-diagram. Create a finished, exceptionally polished product-concept illustration for a professional open-source Agent observability README. Landscape 4:3 canvas, highest practical native resolution, crisp readable text. Sophisticated dark navy #0c1220 editorial design, warm white typography, lavender #bba7ff, mint #94e6c0 and restrained amber for anomalies. Precise interface mockups, layered inspector panels, intentional asymmetrical hierarchy, fine rules, generous inner padding, sharp orthogonal connectors. Make the interfaces the visual evidence rather than decorations. NO product logos, no invented symbols or wordmarks, no circular brand marks, no AI Vision lettering, no vendor icons, no photographs, robots, floating 3D, slogans not supplied, fake customer claims or invented percentages. Generic functional icons are allowed. Text-only technical names are allowed. Do not claim a Chinese UI or Chinese support. This is a CONCEPT illustration, not a screenshot; visibly label it. Use ONLY the supplied copy and necessary short technical UI labels. Do not add filler paragraphs or tiny illegible annotations. All labels must fit.
Topic 02: three ways to understand the SAME run, with actual meaningful visual structures. No dashboards of aggregate metrics, no diagnosis job UI.
Exact large heading: "同一次执行，换个视角就清楚了"
Subtitle: "执行层级、调用关系、耗时分布，放在一起看".
Give 65% of the body to one dominant left "Chain · 执行树" panel and a right detail inspector. Under this, give a wide shorter band to two distinct panels "Graph · 调用关系" and "Flow / Timeline · 耗时分布".
The same example trace in every view is the task "查询订单". Its execution tree root "Agent" has four time-ordered child steps: "模型规划", "查询工具" with amber "失败", "重试工具" with mint "完成", "模型回答". Indentation and hierarchy should be precise; visually select "重试工具" in lavender. Details inspector selected step "重试工具", compact label pairs "输入 / 输出", "状态 / 耗时", "Token / 来源". Do not put fake token totals or timestamps. A short callout "回到上下文，判断重试是否必要" connects selected step with input/output area.
The Graph panel shows exactly the SAME task nodes as a meaningful graph with parent-child links and left-to-right ordered siblings; highlight query-failure node and retry node, avoid a fake parallel branch.
The Flow / Timeline panel labels its four rows "模型规划", "查询工具", "重试工具", "模型回答" and shows sequential horizontal bars at distinct chronological offsets along a left-to-right time arrow. No numerical timing.
Below panels put a concise takeaway: "不只看到报错，更能理解它如何影响后续执行".
Footer: "概念示意 · 可查看内容取决于采集来源与模式".
Do not illustrate MCP/AGENTS.md/A2A/Jev dedicated parsing panels, because they remain an enhancement direction described separately in text.
```

## improve.zh-CN

```text
Use case: infographic-diagram. Create a finished, exceptionally polished product-concept illustration for a professional open-source Agent observability README. Landscape 4:3 canvas, highest practical native resolution, crisp readable text. Sophisticated dark navy #0c1220 editorial design, warm white typography, lavender #bba7ff, mint #94e6c0 and restrained amber for anomalies. Precise interface mockups, layered inspector panels, intentional asymmetrical hierarchy, fine rules, generous inner padding, sharp orthogonal connectors. Make the interfaces the visual evidence rather than decorations. NO product logos, no invented symbols or wordmarks, no circular brand marks, no AI Vision lettering, no vendor icons, no photographs, robots, floating 3D, slogans not supplied, fake customer claims or invented percentages. Generic functional icons are allowed. Text-only technical names are allowed. Do not claim a Chinese UI or Chinese support. This is a CONCEPT illustration, not a screenshot; visibly label it. Use ONLY the supplied copy and necessary short technical UI labels. Do not add filler paragraphs or tiny illegible annotations. All labels must fit.
Topic 03: a persistent diagnostic workflow at team scope, not a repeat of the single missing-verification story. Visually explain selection → evidence review → quality comparison.
Exact large heading: "把偶然发现的问题，变成持续改进的依据"
Subtitle: "选定任务范围 → 运行诊断 → 复核 Badcase → 比较下一轮".
Distinct asymmetric three-part composition: upper-left compact diagnostic job panel, upper-right larger detailed evidence review, lower wide comparison panel. A thin curved return arrow from lower comparison back to next diagnostic run may represent iteration; never a brand logo.
Panel "在线诊断": filter chips "任务范围" "诊断器", toggles "按需 / 定时"; three checked checks "重复调用" "工具失败" "缺少验证". Precise fine connector to the next panel.
Panel "Badcase 复核": active hypothetical finding "同一工具被反复调用", subheading "摘要与执行依据"; show three repeated tool rows "search → 相同参数", "search → 相同参数", "search → 相同参数", with a visible connected trace excerpt and a small pill "回查原始轨迹". Human review controls "确认问题" "误报" "无法判断", plus "复核备注". Do NOT conclude repetition is always a real issue; need human review.
Between top and bottom a clear action label "团队调整指令或流程，再运行同类任务".
Bottom "质量对比": columns "调整前" / "调整后", rows "质量分数与趋势", "同类问题是否复现", "实际交付", "Token 与耗时". Use before/after outline mini-charts and short bars with no numbers. Charts need NOT always improve; different rows can rise, fall or stay similar. A prominent comparison-rule pill "同类任务 · 同一组检查". This is a conceptual comparison, never a claim of savings or automated repair.
Takeaway "诊断给线索，复核做判断，对比看效果".
Footer: "概念示意 · 诊断分数不替代任务验收".
Keep readable UI mockups and meaningful details without dense paragraphs.
```

## observe.en

```text
Use case: text-localization. The supplied image is the edit target. Make a fully localized edition of this exact professional README concept illustration. Preserve all composition, hierarchy, dark navy/lavender/mint/amber colors, precise panels, arrows, chart semantics and aspect ratio 4:3. Retypeset every Chinese string, including tiny UI labels, dropdowns, notes, JSON example values, finding descriptions and footer. Use natural typography, legible text, adjust line breaks to fit without cropping. No product logos or wordmarks, no newly invented branding; retain only generic functional icons. No Chinese UI/support promotion in any part of the image. No invented metrics or new capabilities; charts are conceptual only. Highest practical native resolution. Create the ENGLISH edition. Exact heading "Your Agents deserve to be seen". Subtitle "Connect apps and frameworks → Inspect usage → Investigate a run".
"Agent apps", "Agent frameworks", "Collection plugins", "Langfuse SDK / NeMo Relay", "Shared trace stream", "Runtime and usage". Retain all plain technical names in the two lanes.
Dashboard "Runtime and usage"; controls "Space" "All", "Agent" "All", "Time range" "Last 7 days".
Metrics "Model requests", "Tool calls", "Tokens", "Available cost". "Usage distribution"; legend "Apps" / "Frameworks"; rows "Apps / frameworks", "Models", "Tools". "Runs worth investigating" with "Many retries", "High latency", "Tool failures". Drilldown "Trace → Related steps", "Put usage in context".
Note "Using Langfuse? Change endpoint and credentials; reuse supported instrumentation." Footer "Concept illustration. Fields depend on collection source. Unknown usage is not zero."
```

## observe.ja

```text
Use case: text-localization. The supplied image is the edit target. Make a fully localized edition of this exact professional README concept illustration. Preserve all composition, hierarchy, dark navy/lavender/mint/amber colors, precise panels, arrows, chart semantics and aspect ratio 4:3. Retypeset every Chinese string, including tiny UI labels, dropdowns, notes, JSON example values, finding descriptions and footer. Use natural typography, legible text, adjust line breaks to fit without cropping. No product logos or wordmarks, no newly invented branding; retain only generic functional icons. No Chinese UI/support promotion in any part of the image. No invented metrics or new capabilities; charts are conceptual only. Highest practical native resolution. Create the JAPANESE edition. Exact heading "使っている Agent の実行を、見えるように"
Subtitle "アプリ・フレームワークの接続 → 使用量の把握 → 実行を確認".
"Agent アプリ", "開発フレームワーク", "収集プラグイン", "Langfuse SDK / NeMo Relay", "トレースを一元化", "実行・使用量ダッシュボード". Keep all plain technical names unchanged.
Dashboard controls "スペース" "すべて", "Agent" "すべて", "期間" "直近 7 日間".
Metrics "モデルリクエスト", "ツール呼び出し", "Token", "取得可能なコスト".
"使用量の分布"; legend "アプリ" / "フレームワーク"; rows "アプリ / フレームワーク", "モデル", "ツール".
"確認すべき実行": "再試行が多い", "時間がかかる", "ツールの失敗".
Drilldown "Trace → 関連ステップ", "使用量をタスクの文脈へ".
Note "Langfuse の計装済みなら、送信先と認証情報を変更して対応する収集経路を活用".
Footer "概念図。取得項目は収集元によって異なり、不明な使用量はゼロではありません。"
```

## understand.en

```text
Use case: text-localization. The supplied image is the edit target. Make a fully localized edition of this exact professional README concept illustration. Preserve all composition, hierarchy, dark navy/lavender/mint/amber colors, precise panels, arrows, chart semantics and aspect ratio 4:3. Retypeset every Chinese string, including tiny UI labels, dropdowns, notes, JSON example values, finding descriptions and footer. Use natural typography, legible text, adjust line breaks to fit without cropping. No product logos or wordmarks, no newly invented branding; retain only generic functional icons. No Chinese UI/support promotion in any part of the image. No invented metrics or new capabilities; charts are conceptual only. Highest practical native resolution. Create the ENGLISH edition. Exact heading "One run. The right view makes it clear."
Subtitle "Read the hierarchy, call relationships and timing together."
Panels "Chain · Execution tree", "Graph · Call relationships", "Flow / Timeline · Duration", "Step inspector".
Task root "Agent · Look up order". All views use four matching steps "Plan", "Query tool" (Failed), "Retry tool" (Done), "Answer" (Done).
Correct the source image's two status inconsistencies: root Agent must be "Done", not running, and final Answer must be "Done", not pending, in BOTH Chain and Graph. Timeline therefore represents the completed run. Failed initial query remains Failed.
Inspector selected "Retry tool", "Done"; callout "Use context to judge whether the retry was needed". Input JSON values in English: query "look up order", reason "previous call failed"; output result "order information returned". Labels "Input" "Output" "Status" "Duration" "Tokens" "Source"; unavailable values as dashes.
Time axis "Time →".
Takeaway "See an error—and understand its effect on the rest of the run."
Footer "Concept illustration. Visible details depend on collection source and mode."
```

## understand.ja

```text
Use case: text-localization. The supplied image is the edit target. Make a fully localized edition of this exact professional README concept illustration. Preserve all composition, hierarchy, dark navy/lavender/mint/amber colors, precise panels, arrows, chart semantics and aspect ratio 4:3. Retypeset every Chinese string, including tiny UI labels, dropdowns, notes, JSON example values, finding descriptions and footer. Use natural typography, legible text, adjust line breaks to fit without cropping. No product logos or wordmarks, no newly invented branding; retain only generic functional icons. No Chinese UI/support promotion in any part of the image. No invented metrics or new capabilities; charts are conceptual only. Highest practical native resolution. Create the JAPANESE edition. Exact heading "同じ実行も、視点を変えると見えてくる"
Subtitle "実行の階層・呼び出しの関係・所要時間を一緒に読む".
Panels "Chain · 実行ツリー", "Graph · 呼び出し関係", "Flow / Timeline · 所要時間", "ステップの詳細".
Task root "Agent · 注文を照会". All views use four matching steps "モデルの計画", "照会ツール" (失敗), "再試行" (完了), "モデルの回答" (完了).
Correct the source image's two status inconsistencies: root Agent must show "完了", not running, final model answer must show "完了", not pending, in BOTH Chain and Graph. Failed initial query stays "失敗". Timeline depicts a completed run.
Inspector selected "再試行", "完了"; callout "前後の文脈から、再試行の必要性を確かめる".
Input JSON values translated: query "注文を照会", reason "前の呼び出しが失敗"; output result "注文情報を取得".
Labels "入力" "出力" "状態" "所要時間" "Token" "収集元"; unavailable values as dashes. Time axis "時間 →".
Takeaway "エラーの発生だけでなく、その後の実行への影響まで理解する".
Footer "概念図。確認できる内容は収集元と収集モードによって異なります。"
```

## improve.en

```text
Use case: text-localization. The supplied image is the edit target. Make a fully localized edition of this exact professional README concept illustration. Preserve all composition, hierarchy, dark navy/lavender/mint/amber colors, precise panels, arrows, chart semantics and aspect ratio 4:3. Retypeset every Chinese string, including tiny UI labels, dropdowns, notes, JSON example values, finding descriptions and footer. Use natural typography, legible text, adjust line breaks to fit without cropping. No product logos or wordmarks, no newly invented branding; retain only generic functional icons. No Chinese UI/support promotion in any part of the image. No invented metrics or new capabilities; charts are conceptual only. Highest practical native resolution. Create the ENGLISH edition. Heading "Turn isolated findings into evidence for better runs"
Subtitle "Choose tasks → Run diagnostics → Review findings → Compare the next run".
Panel 1 "Online diagnostics", caption "Choose scope and checks". "Task scope", "All tasks", "Last 7 days", "All Agents"; "Diagnosers": "Repeated calls", "Tool failures", "Missing verification"; "Run mode", "On demand", "Scheduled", "Run diagnostics".
Panel 2 "Bad-case review", caption "Inspect evidence. Make a human judgment." Finding "The same tool is called repeatedly"; "Summary and evidence". Three rows "search → same parameters". "Open source trace", "Trace excerpt". Human controls "Confirmed", "False positive", "Uncertain"; "Review notes". Translate small finding list and placeholder. The trace code remains English. No implication repeated calls always mean a problem.
Between panels "Your team changes instructions or workflow, then reruns similar tasks". Return arrow "Next diagnostic run".
Panel 3 "Quality comparison". Pill "Similar tasks · Same checks". Columns "Measure", "Before", "After", "Trend". Rows "Quality scores and trends", "Do the same issues recur?", "Task results", "Tokens and latency". Existing final labels naturally translated "Higher", "Fewer recurrences", "Similar", "Slightly higher". These are an illustrative example, not measured product improvement.
Takeaway "Diagnostics find clues. Review confirms. Comparison tests the change."
Footer "Concept illustration. Diagnostic scores do not replace task acceptance."
```

## improve.ja

```text
Use case: text-localization. The supplied image is the edit target. Make a fully localized edition of this exact professional README concept illustration. Preserve all composition, hierarchy, dark navy/lavender/mint/amber colors, precise panels, arrows, chart semantics and aspect ratio 4:3. Retypeset every Chinese string, including tiny UI labels, dropdowns, notes, JSON example values, finding descriptions and footer. Use natural typography, legible text, adjust line breaks to fit without cropping. No product logos or wordmarks, no newly invented branding; retain only generic functional icons. No Chinese UI/support promotion in any part of the image. No invented metrics or new capabilities; charts are conceptual only. Highest practical native resolution. Create the JAPANESE edition. Heading "個別の気づきを、継続的な改善の根拠へ"
Subtitle "対象を選ぶ → 診断を実行 → Badcase をレビュー → 次の実行と比較".
Panel 1 "オンライン診断", caption "対象と診断器を選ぶ". "対象範囲", "すべてのタスク", "直近 7 日間", "すべての Agent"; "診断器": "重複呼び出し", "ツールの失敗", "検証不足". "実行方法", "手動", "定期", "診断を実行".
Panel 2 "Badcase レビュー", caption "根拠を確認し、人が判断する". Finding "同じツールを繰り返し呼び出している"; "要約と実行の根拠"; three rows "search → 同じ引数". "元のトレースを開く", "トレースの抜粋". Human controls "問題を確認", "誤検知", "判断不可"; "レビューコメント". Translate every small finding label and placeholder. Trace code remains English. Never imply every repeat is a problem.
Between panels "チームが指示や手順を見直し、同種のタスクを再実行". Return arrow "次の診断へ".
Panel 3 "品質比較". Pill "同種のタスク・同じチェック". Columns "比較項目", "変更前", "変更後", "傾向". Rows "品質スコアと推移", "同じ問題の再発", "実際の成果", "Token と所要時間". Trend labels "上昇", "再発が減少", "ほぼ同じ", "やや増加". Illustration only, no measured promise.
Takeaway "診断で手がかりを得て、レビューで判断し、比較で効果を確かめる".
Footer "概念図。診断スコアはタスクの受け入れ確認に代わるものではありません。"
```

## understand.zh-CN.correction

```text
Use case: precise-object-edit. The supplied image is the edit target. Preserve this exact Chinese 4:3 dark README concept illustration: all layout, arrows, panels, typography, diagrams and text. Fix only the inconsistent completion states for this finished example run. In Chain, root Agent status "执行中" becomes "完成"; final row 模型回答 status "待执行" becomes "完成". In Graph, final 模型回答 status "待执行" becomes "完成" with mint dot. The original 查询工具 failure stays 失败. Preserve all other states, labels, Inspector and Timeline unchanged. No new branding, icons or product logos. Highest practical native resolution.
```

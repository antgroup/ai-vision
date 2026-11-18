# AI Vision Design Tokens（亮色模式）

> 来源：设计师提供的 UI 设计规范，最后更新 2026-03-25
>
> 本文件是 AI 生成前端代码的**唯一色彩/字体/圆角/间距参考**，禁止使用规范之外的值。

---

## 1. 颜色系统（Color）

### 1.1 文本颜色

| Token 名 | 色值 | 用途 |
|-----------|------|------|
| `--color-text-primary` | `rgba(0, 10, 26, 0.88)` | 主题色、标题、主按钮文字、次按钮文本 |
| `--color-text-secondary` | `rgba(0, 10, 26, 0.65)` | 次要文本 |
| `--color-text-description` | `rgba(0, 10, 26, 0.45)` | 描述性文字、placeholder |
| `--color-text-disabled` | `rgba(0, 10, 26, 0.25)` | 禁用态文本 |

### 1.2 图标颜色

| Token 名 | 色值 | 用途 |
|-----------|------|------|
| `--color-icon-primary` | `rgba(0, 10, 26, 0.88)` | 一级菜单图标 |
| `--color-icon-normal` | `rgba(0, 10, 26, 0.65)` | 常规图标 |
| `--color-icon-disabled` | `rgba(0, 10, 26, 0.25)` | 禁用态图标 |

### 1.3 语义色（状态色）

| Token 名 | 色值 | 用途 |
|-----------|------|------|
| `--color-success` | `#17A34A` | 成功状态的文字和图标 |
| `--color-error` | `#EE4444` | 失败/危险状态的文字和图标 |
| `--color-warning` | `#EA5706` | 告警状态的文字和图标 |
| `--color-auxiliary` | `#9335E9` | 辅助色，其他状态补充 |

### 1.4 背景颜色

| Token 名 | 色值 | 用途 |
|-----------|------|------|
| `--color-bg-page` | `#FFFFFF` | 页面主背景 |
| `--color-bg-sidebar` | `#F9F9F9` | 左侧菜单背景 |
| `--color-bg-hover` | `rgba(0, 10, 26, 0.04)` | 一级菜单选中、空间选中、分段控制器背景、悬停图标底色 |
| `--color-bg-subtle` | `rgba(0, 10, 26, 0.02)` | 弱背景：区块背景、表格列头背景 |

### 1.5 边框颜色

| Token 名 | 色值 | 用途 |
|-----------|------|------|
| `--color-border` | `#E0E6EE` | 边框、分割线 |
| `--color-border-error` | `#EE4444` | 危险/异常容器的描边 |

### 1.6 容器样式

> 容器 = 填充色 + 描边。以下是完整的容器组合方式：

| 容器类型 | 填充 | 描边 | CSS 示例 |
|----------|------|------|----------|
| **基础容器** | `#FFFFFF` | `1px solid #E0E6EE` | `background: #fff; border: 1px solid #E0E6EE;` |
| **失效/禁用** | `rgba(0,10,26,0.04)` | `1px solid #E0E6EE` | `background: rgba(0,10,26,0.04); border: 1px solid #E0E6EE;` |
| **危险/异常** | `#FFFFFF` | `1px solid #EE4444` | `background: #fff; border: 1px solid #EE4444;` |
| **分段控制器选中态** | `#FFFFFF` | 无描边 | `background: #fff;` |
| **成功状态容器** | `rgba(23,163,74,0.1)` | 无描边 | `background: rgba(23,163,74,0.1);` |
| **失败状态容器** | `rgba(238,68,68,0.1)` | 无描边 | `background: rgba(238,68,68,0.1);` |
| **告警状态容器** | `rgba(234,87,6,0.1)` | 无描边 | `background: rgba(234,87,6,0.1);` |
| **提示容器** | `rgba(0,10,26,0.04)` | 无描边 | `background: rgba(0,10,26,0.04);` |
| **其他状态容器** | `rgba(147,53,233,0.1)` | 无描边 | `background: rgba(147,53,233,0.1);` |

---

## 2. 字体系统（Typography）

### 2.1 字体族

| Token 名 | 字体 | 用途 |
|-----------|------|------|
| `--font-family-base` | `"PingFang SC", -apple-system, sans-serif` | 页面所有中文及默认文本 |
| `--font-family-number` | `"Alibaba Sans 102", "DIN Alternate", sans-serif` | 强调统计数值场景 |
| `--font-family-code` | `"Menlo", "Consolas", monospace` | 代码片段 |

### 2.2 字阶与行高

| Token 名 | 字号 | 行高 | 用途 |
|-----------|------|------|------|
| `--font-size-xl` | `24px` | `32px` | 超大字体，起强调作用（Large Title） |
| `--font-size-lg` | `20px` | `28px` | 页容器一级标题（Title） |
| `--font-size-md` | `16px` | `24px` | 二级标题、卡片标题、容器标题（Head） |
| `--font-size-base` | `14px` | `22px` | 正文（Body） |
| `--font-size-sm` | `12px` | `20px` | 描述性文字、tag文字（Caption） |

### 2.3 字重

| Token 名 | 权重值 | 用途 |
|-----------|--------|------|
| `--font-weight-bold` | `600` | 卡片统计数字 |
| `--font-weight-medium` | `500` | 标题 |
| `--font-weight-regular` | `400` | 正文、描述文字、tag文字 |

### 2.4 完整字体组合速查

> 以下是每个字阶的完整用法矩阵，直接复制使用：

#### Large Title（24px）
```css
/* 标题 */     font: 500 24px/32px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 次要 */     font: 400 24px/32px var(--font-family-base); color: rgba(0,10,26,0.65);
/* 描述 */     font: 400 24px/32px var(--font-family-base); color: rgba(0,10,26,0.45);
/* 禁用 */     font: 400 24px/32px var(--font-family-base); color: rgba(0,10,26,0.25);
```

#### Title（20px）
```css
/* 标题 */     font: 500 20px/28px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 正文 */     font: 400 20px/28px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 描述 */     font: 400 20px/28px var(--font-family-base); color: rgba(0,10,26,0.45);
/* 禁用 */     font: 400 20px/28px var(--font-family-base); color: rgba(0,10,26,0.25);
```

#### Head（16px）
```css
/* 标题 */     font: 500 16px/24px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 正文 */     font: 400 16px/24px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 描述 */     font: 400 16px/24px var(--font-family-base); color: rgba(0,10,26,0.45);
/* 禁用 */     font: 400 16px/24px var(--font-family-base); color: rgba(0,10,26,0.25);
/* 成功 */     font: 400 16px/24px var(--font-family-base); color: #17A34A;
/* 告警 */     font: 400 16px/24px var(--font-family-base); color: #EA5706;
/* 错误 */     font: 400 16px/24px var(--font-family-base); color: #EE4444;
```

#### Body（14px）
```css
/* 标题 */     font: 500 14px/22px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 正文 */     font: 400 14px/22px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 描述 */     font: 400 14px/22px var(--font-family-base); color: rgba(0,10,26,0.45);
/* 成功 */     font: 400 14px/22px var(--font-family-base); color: #17A34A;
/* 告警 */     font: 400 14px/22px var(--font-family-base); color: #EA5706;
/* 错误 */     font: 400 14px/22px var(--font-family-base); color: #EE4444;
```

#### Caption（12px）
```css
/* 标题 */     font: 500 12px/20px var(--font-family-base); color: rgba(0,10,26,0.88);
/* 次要 */     font: 400 12px/20px var(--font-family-base); color: rgba(0,10,26,0.65);
/* 描述 */     font: 400 12px/20px var(--font-family-base); color: rgba(0,10,26,0.45);
/* 成功 */     font: 400 12px/20px var(--font-family-base); color: #17A34A;
/* 告警 */     font: 400 12px/20px var(--font-family-base); color: #EA5706;
/* 错误 */     font: 400 12px/20px var(--font-family-base); color: #EE4444;
```

---

## 3. 圆角系统（Border Radius）

| Token 名 | 值 | 组件高度范围 | 典型场景 |
|-----------|-----|-------------|----------|
| `--radius-sm` | `6px` | h ≤ 24px | 复选框 checkbox、标签 tag |
| `--radius-md` | `8px` | 24px < h ≤ 28px | 分段控制器选中态 |
| `--radius-lg` | `10px` | 28px < h ≤ 32px | 常规按钮、选择器、输入框、全局反馈、全局提示 |
| `--radius-xl` | `12px` | h > 32px | 卡片、对话框、区块容器 |

**选择规则**：根据组件实际渲染高度选择对应圆角值，不可混用。

---

## 4. 间距系统（Spacing）

### 4.1 基础单位

**4px 基础网格** — 所有间距必须是 4px 的整数倍。

### 4.2 内边距（Padding）

组件及容器内部边距、元素之间的间距：

| Token 名 | 值 | 用途 |
|-----------|-----|------|
| `--spacing-1` | `1px` | 极小间距（分割线间距） |
| `--spacing-4` | `4px` | 小内边距（1× 基础单位） |
| `--spacing-8` | `8px` | 小内边距（2× 基础单位） |
| `--spacing-12` | `12px` | 中内边距（3× 基础单位） |
| `--spacing-16` | `16px` | **标准内边距**（4× 基础单位） |
| `--spacing-24` | `24px` | 大内边距（6× 基础单位） |

### 4.3 外边距（Margin）

组件与页容器、组件与组件之间的间距：

| Token 名 | 值 | 用途 |
|-----------|-----|------|
| `--margin-1` | `1px` | 极小外边距（分割线间距） |
| `--margin-16` | `16px` | 标准外边距（4× 基础单位） |
| `--margin-24` | `24px` | 中等外边距（6× 基础单位） |

### 4.4 间距使用指南

- **紧密关联的元素**（如图标与文字）：4px 或 8px
- **常规布局间距**（如表单项之间）：12px 或 16px
- **区块分隔**（如卡片之间、大章节之间）：24px 或 32px

---

## 5. CSS 变量快速模板

> 研发同学可以直接将以下代码复制到项目全局样式中：

```css
:root {
  /* ===== 颜色 ===== */
  /* 文本 */
  --color-text-primary: rgba(0, 10, 26, 0.88);
  --color-text-secondary: rgba(0, 10, 26, 0.65);
  --color-text-description: rgba(0, 10, 26, 0.45);
  --color-text-disabled: rgba(0, 10, 26, 0.25);

  /* 图标 */
  --color-icon-primary: rgba(0, 10, 26, 0.88);
  --color-icon-normal: rgba(0, 10, 26, 0.65);
  --color-icon-disabled: rgba(0, 10, 26, 0.25);

  /* 语义色 */
  --color-success: #17A34A;
  --color-error: #EE4444;
  --color-warning: #EA5706;
  --color-auxiliary: #9335E9;

  /* 背景 */
  --color-bg-page: #FFFFFF;
  --color-bg-sidebar: #F9F9F9;
  --color-bg-hover: rgba(0, 10, 26, 0.04);
  --color-bg-subtle: rgba(0, 10, 26, 0.02);

  /* 边框 */
  --color-border: #E0E6EE;
  --color-border-error: #EE4444;

  /* 状态容器背景（10% 透明度） */
  --color-bg-success: rgba(23, 163, 74, 0.1);
  --color-bg-error: rgba(238, 68, 68, 0.1);
  --color-bg-warning: rgba(234, 87, 6, 0.1);
  --color-bg-info: rgba(0, 10, 26, 0.04);
  --color-bg-auxiliary: rgba(147, 53, 233, 0.1);

  /* ===== 字体 ===== */
  --font-family-base: "PingFang SC", -apple-system, "Helvetica Neue", sans-serif;
  --font-family-number: "Alibaba Sans 102", "DIN Alternate", sans-serif;
  --font-family-code: "Menlo", "Consolas", "Courier New", monospace;

  --font-size-xl: 24px;
  --font-size-lg: 20px;
  --font-size-md: 16px;
  --font-size-base: 14px;
  --font-size-sm: 12px;

  --line-height-xl: 32px;
  --line-height-lg: 28px;
  --line-height-md: 24px;
  --line-height-base: 22px;
  --line-height-sm: 20px;

  --font-weight-bold: 600;
  --font-weight-medium: 500;
  --font-weight-regular: 400;

  /* ===== 圆角 ===== */
  --radius-sm: 6px;   /* h ≤ 24px: checkbox, tag */
  --radius-md: 8px;   /* 24 < h ≤ 28: 分段控制器 */
  --radius-lg: 10px;  /* 28 < h ≤ 32: 按钮, 输入框, 选择器 */
  --radius-xl: 12px;  /* h > 32: 卡片, 对话框, 区块 */

  /* ===== 间距 ===== */
  --spacing-1: 1px;
  --spacing-4: 4px;
  --spacing-8: 8px;
  --spacing-12: 12px;
  --spacing-16: 16px;
  --spacing-24: 24px;
  --spacing-32: 32px;
}
```

---

## 6. 常见组件样式速查

> 以下为常见组件的推荐样式，直接参照使用：

### 主按钮（Primary Button）
```css
background: rgba(0, 10, 26, 0.88);
color: #FFFFFF;
border: none;
border-radius: 10px;        /* 常规按钮高度 28-32px → radius-lg */
font: 500 14px/22px var(--font-family-base);
padding: 8px 16px;
```

### 次按钮（Secondary Button）
```css
background: #FFFFFF;
color: rgba(0, 10, 26, 0.88);
border: 1px solid #E0E6EE;
border-radius: 10px;
font: 500 14px/22px var(--font-family-base);
padding: 8px 16px;
```

### 输入框（Input）
```css
background: #FFFFFF;
border: 1px solid #E0E6EE;
border-radius: 10px;
font: 400 14px/22px var(--font-family-base);
color: rgba(0, 10, 26, 0.88);
padding: 8px 12px;
```
```css
/* placeholder */
color: rgba(0, 10, 26, 0.45);
```

### 卡片（Card）
```css
background: #FFFFFF;
border: 1px solid #E0E6EE;
border-radius: 12px;         /* 卡片高度 > 32px → radius-xl */
padding: 16px;
```

### Tag / 标签
```css
border-radius: 6px;          /* tag 高度 ≤ 24px → radius-sm */
font: 400 12px/20px var(--font-family-base);
padding: 2px 8px;
```

### 成功状态 Tag
```css
background: rgba(23, 163, 74, 0.1);
color: #17A34A;
border: none;
```

### 失败状态 Tag
```css
background: rgba(238, 68, 68, 0.1);
color: #EE4444;
border: none;
```

### 告警状态 Tag
```css
background: rgba(234, 87, 6, 0.1);
color: #EA5706;
border: none;
```

### 表格列头
```css
background: rgba(0, 10, 26, 0.02);
font: 500 14px/22px var(--font-family-base);
color: rgba(0, 10, 26, 0.65);
```

### 分割线
```css
border-top: 1px solid #E0E6EE;
```

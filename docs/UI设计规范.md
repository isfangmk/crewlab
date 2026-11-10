# AI 工作台 UI 设计规范（列表与详情改造）

> 版本：v1.1 · 2026-11-10  
> 来源：可观测页（`/observability`）与 Trace 详情改造后形成的视觉语言。  
> 目标：所有管理页使用同一套骨架、字阶、状态语义与分页交互，减少页面间的「换个页面换种风格」。  
> v1.1 变更：同步批次 10–12 与进度行；组件清单补 `Toast` / `ConfirmDialog`；修正会话摘要口径；**列表时间一律 `fullDateTime`，取消相对时间**。

---

## 1. 设计原则

1. **信息密度优先**：一屏能扫读更多行；主信息一行、元信息一行，避免大段说明挤占表格。
2. **一个页面只有一张筛选卡**：搜索、筛选胶囊、已生效条件都在同一张卡里，不再散落成多行工具栏。
3. **状态用颜色说话**：状态胶囊固定 5 种语义色，同一状态在全站不换颜色。
4. **卡片不是装饰**：卡片只用于分组和可点击容器；表格行不再套卡片。
5. **交互可撤销**：任何筛选条件都能单击移除，并提供「清除全部」。
6. **暗/亮双主题**：新样式沿用现有 `html.light` 覆盖层，不新增固定深色背景。

---

## 2. 页面骨架

标准列表页自上而下四段，间距固定：

```text
PageHeader            标题 / 副标题 / 主操作
FilterBar             搜索 + 筛选胶囊（+ 第二行已生效条件）
MetricCard 网格        可选；仅统计类页面使用
TableCard / 卡片网格    数据区
TablePager            分页（客户端分页时才需要）
```

### 2.1 PageHeader

- 标题：`text-[15px] font-semibold tracking-tight`，副标题：`text-[11.5px] text-zinc-500`。
- 主操作放右侧，按钮统一 `size="sm"`；破坏性操作（批量删除）只在有选中项时出现。
- 页头已由 `PageHeader` 统一，页面不要再自己写 `h1`。

### 2.2 FilterBar

- 卡片：`rounded-xl border border-zinc-800/60 bg-zinc-900/40`，页面外层给 `mx-6 mt-3`。
- 第一行：搜索框默认 `flex-1 min-w-[15rem]`，其余为筛选胶囊。
- 第二行（`footer`）：自定义时间、已生效条件、清除全部。
- 筛选条件生效时：胶囊描边与文字变 indigo，并出现右上角清除按钮。

### 2.3 MetricCard

- 网格：`grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6`，仅统计页使用。
- 内容：18px 图标块 + 11px 标签 + 19px 数值 + 10px 注脚；需要占比时加 2px 进度条。
- 失败率、错误数等异常指标用 `danger`，工具/技能调用用 `ok` / `accent`。
- 传入 `onClick` 后整卡变为筛选入口（`aria-pressed` + 高亮描边），用于审批状态、工具风险这类固定枚举。

### 2.4 TableCard

- 表格必须放进 `TableCard`，由它负责圆角、边框和滚动容器。
- 表头：`text-[11px] text-zinc-500`，单元格 `px-3 py-2`，不加全部大写。
- 行：两行结构。  
  第一行主信息（12.5px，失败时红色）；第二行元信息（10px，会话短链 / 助理 / 模型 / 时间）。
- 分组表（工具、技能）：分组行 `bg-zinc-950/50`，可折叠，组内二维码使用 `pl-8` 缩进。
- 有分页时通过 `footer={<TablePager .../>}` 传入，分页条与表体共用边框。

### 2.5 详情弹窗

- 沿用 Trace 详情的「诊断报告」结构：头部状态 + 主指标、分段标题、键值网格、可复制代码块。
- 弹窗标题只放对象名；状态、耗时等上下文放正文头部，不塞进标题。

---

## 3. 视觉 Token

| 项 | 规则 |
| --- | --- |
| 卡片表面 | `rounded-xl border border-zinc-800/60 bg-zinc-900/40` |
| 表格容器 | `rounded-xl border border-zinc-800/60 bg-zinc-900/30` |
| 卡内分组 | `rounded-lg border border-zinc-800/60 bg-zinc-950/60` |
| 主文字 | `text-zinc-100` / `text-zinc-200` |
| 次文字 | `text-zinc-400` / `text-zinc-500` |
| 弱文字 | `text-zinc-600` |
| 行主字号 | 12.5px；元信息 10–11px；页标题 15px；指标 19px |
| 单元格 | `px-3 py-2`；筛选卡 `px-3 py-2.5` |
| 圆角 | 页面卡片 12px；胶囊 8px；状态胶囊全圆角 |
| 图标 | 列表内 11–13px，操作按钮 13px，不使用多色图标 |
| 强调色 | indigo（选中 / 链接 / 可清除态） |

### 状态语义

| tone | 颜色 | 适用 |
| --- | --- | --- |
| `ok` | 绿 | 成功、已执行、已批准、安全、活跃 |
| `warn` | 琥珀 | 待处理、需审批、已暂停、变更类技能 |
| `danger` | 红 | 失败、危险操作、拒绝后异常 |
| `info` | 蓝 | 进行中、置顶、运行态 |
| `neutral` | 灰 | 已拒绝、已过期、归档等终态 |

状态胶囊统一用 `StatusPill`，圆点 + 文案；进行中可加 `pulse`。

---

## 4. 组件清单

组件一律从 `@/components/ui` 导入（唯一例外是 `ConfirmDialog`，见下表）；页面禁止再复制粘贴同类 class。

| 组件 | 用途 |
| --- | --- |
| `PageHeader` | 页面标题区 |
| `FilterBar` | 筛选卡容器；`footer` 放第二行 |
| `FilterPill` | 字段名 + 下拉/输入的组合筛选 |
| `ChoiceChip` | 固定枚举单选（风险、类别、审批状态） |
| `FilterChip` | 已生效条件，点击即撤销 |
| `SearchInput` | 搜索框（放大镜 + 清空按钮 + Enter 回调） |
| `MetricCard` | 顶部统计卡 |
| `StatusPill` | 状态胶囊 |
| `TableCard` | 表格容器 |
| `TablePager` | 分页条 |
| `RowAction` | 行内文字操作（批准 / 运行 / 拒绝…） |
| `RowActionIcon` | 行内图标操作（编辑 / 删除 / 置顶…） |
| `SectionCard` | 设置 / 详情分组卡（标题 + 说明 + footer） |
| `DetailSection` | 详情页分组标题（11px 大写，可 danger） |
| `DetailStat` / `DetailStatGrid` | 详情页指标格 |
| `CodeBlock` | payload / result / JSON 代码块 |
| `MetaRow` | 等宽键值行（可复制） |
| `Card` | 通用卡片（卡片网格 / 详情分组） |
| `EmptyStateCard` | 空状态 |
| `Toast` | 右下角轻提示（成功 / 失败反馈，5 秒自动消失）；不需要用户决策的反馈用它，替代 `window.alert` |
| `ConfirmDialog` | 破坏性操作二次确认：`Modal` + 取消 / 危险按钮 + 行内错误，执行中禁关闭；位于 `components/ConfirmDialog.tsx`，不从 ui 导出 |
| `Select` / `Input` / `Checkbox` / `Switch` | 表单控件 |
| `PersonaFields` / `KnowledgeScopePicker` | 助理编辑页专用复合块（人设四段 / 目录与文档范围），只在 `Agents.tsx` 使用，不进 ui 原语 |
| `Field` / `FieldGroup`（`Agents.tsx` 内部） | 单控件字段 / 复合控件字段。复合控件（一组按钮、列表、多选）必须用 `FieldGroup`（只渲染分组标题的 `<div>`）；用 `<label>` 的 `Field` 包裹会把全部子文本并进第一个按钮的可访问名，读屏与 Testing Library 都取不到唯一目标 |

### 4.0 行内操作按钮（全局唯一规格）

列表 / 表格行末、卡片右上角的操作一律用 `RowAction` 与 `RowActionIcon`，页面不再手写按钮 className。

| 组件 | 规格 | 用途 |
| --- | --- | --- |
| `RowAction` | `h-7`、`px-2`、`gap-1`、`text-[11px]`、图标 12px、`outline` 表面 | 带文案的动作：批准 / 拒绝 / 运行 / 设为默认 |
| `RowActionIcon` | `h-7 w-7`、图标 13px、无边框 `subtle`、hover 底色 | 纯图标动作：编辑 / 删除 / 置顶 / 归档 / 导出 / 复制 |

- 语义色由 `tone` 控制：`neutral`（默认）、`positive`（`text-ok`）、`danger`（红色文字 + hover 红色底）。
- 危险动作只改颜色，不改尺寸与表面，避免行内出现两套高度。
- 同一行的图标动作间距 `gap-0.5`，文字动作间距 `gap-1.5`。
- 卡片级主操作（如「使用此助理」）可保留 indigo 强调色，但高度、内边距、字号必须与 `RowAction` 对齐（`h-7 px-2 text-[11px]`）。

### 4.1 FilterBar 示例

```tsx
<FilterBar
  className="mx-6 mt-3"
  footer={
    activeChips.length > 0 ? (
      <>
        {activeChips.map((chip) => (
          <FilterChip key={chip.key} label={chip.label} onClear={chip.clear} />
        ))}
        <Button variant="ghost" size="sm" onClick={resetFilters}>
          清除全部
        </Button>
      </>
    ) : null
  }
>
  <SearchInput value={q} onChange={setQ} placeholder="搜索…" ariaLabel="搜索" />
  <FilterPill label="状态" active={status !== ''} onClear={() => setStatus('')} width="w-[7.5rem]">
    <Select variant="ghost" value={status} onValueChange={setStatus} options={...} />
  </FilterPill>
</FilterBar>
```

### 4.2 表格 + 分页示例

```tsx
<TableCard
  className="min-h-0 flex-1"
  footer={
    <TablePager
      page={page}
      pageSize={pageSize}
      total={total}
      shown={items.length}
      loading={loading}
      onPageChange={setPage}
      pageSizeOptions={[20, 50, 100]}
      onPageSizeChange={(size) => { setPage(0); setPageSize(size); }}
    />
  }
>
  <table className="og-action-table w-full text-sm">...</table>
</TableCard>
```

---

## 5. 改造进度

### 已完成（第一阶段：列表骨架）

| 页面 | 改动 |
| --- | --- |
| 可观测 `/observability` | 筛选卡、指标卡、状态胶囊、分页抽为共享组件，视觉不变 |
| 会话 `/sessions` | FilterBar + 搜索清空 + 助理/时间筛选胶囊 + 状态胶囊 + TableCard/TablePager |
| 技能 `/skills` | FilterBar + 共享 ChoiceChip + 类别胶囊 + TableCard + 技能等级改用状态胶囊 |
| 助理 `/agents` | 搜索区改为 FilterBar；卡片沿用统一 `Card` 表面 |
| MCP `/mcp` | 搜索区改为 FilterBar（服务列表仍为卡片） |
| 知识库 `/knowledge` | 双栏工作台：左侧来源分组 + 目录树常驻，右侧 `FilterBar`（统一搜索框，回车触发；右侧「来源」胶囊按 manual / upload / repo 收口，与侧栏分组正交）+ `TableCard` 文档表（来源 / 标签彩色胶囊、更新时间列）+ `TablePager` 客户端分页；提交检索后结果表替换文档表，可一键清除回目录 |

### 已完成（第二阶段：指标卡 + 双层列表）

| 页面 | 改动 |
| --- | --- |
| 审批 `/approvals` | 列表形式（不用户卡片）：`TableCard` + 表头「审批内容 / 状态 / 来源 / 提交时间 / 操作」，行内展开 payload/result，提交时间列附带决策时间；服务端分页 + 提交时间筛选 + 提交时间升/降序 + 关键字搜索；顶部 6 张状态指标卡（可点击过滤，统计随搜索/时间联动） |
| 工具 `/tools` | 顶部 3 张风险指标卡（总数 / 安全 / 需审批）+ FilterBar；表格改为「工具/说明」双层行 + 参数统计列 + 行内运行按钮 |

### 第三阶段：全站对齐计划

原则：先抽共享原语，再按「页头 → 详情 → 列表/卡片 → 设置 → 对话」顺序批量替换；每个批次独立可验收、可回滚，不改数据层。

**进度：批次 0–12 已完成。**

| 已完成项 | 结果 |
| --- | --- |
| 共享原语 | `rowActionClass` / `rowActionIconClass`、`SectionCard`、`DetailSection`、`DetailStat` / `DetailStatGrid`、`CodeBlock`、`MetaRow`，全部从 `@/components/ui` 导出 |
| 页头动作 | 审批 / 会话 / 技能 / 助理 / MCP / 知识库 / 工具统一为 28px；技能页「重载 / 刷新」由 FilterBar 上移到页头；审批「返回列表」移入 `leading` |
| 可观测详情 | 内部 `DetailMetric` / `SectionTitle` / `MetaRow` / `CopyButton` 换成共享原语；「打开会话」链接复用 `rowActionClass` |
| 审批详情 | 改为 `leading` 返回 + 状态胶囊 + 4 格元信息 + 操作内容 / 执行结果 / 决策说明三段 + 底部 sticky 操作条；批准 primary、拒绝 `RowAction danger`、刷新 `RowAction` |
| 详情弹窗 | 工具 / 技能 / 助理详情统一 `DetailSection`；参数表统一 11px 表头 + `px-3 py-2` 单元格，并套 `og-action-table` 让浅色模式表头落到 `rgb(248 250 252)`；系统提示与返回示意改用 `CodeBlock`；助理弹窗底部按钮统一 28px |
| 列表与卡片 | 技能表头与工具页对齐；MCP 服务器卡头部与参数表统一 `Chip` / sticky 表头；知识库文档列表收进单容器 `divide-y` 行（中性图标 + 标题 + 一行元信息，来源文字统一用次级灰），目录计数改等宽数字、空态动作 28px；助理卡来源与工具/只读改 `Chip`，标题 12.5px |
| 设置页 | `SettingsLayout` rail 12.5px + indigo 选中态、权限不足改 `EmptyStateCard`；`Preferences` / `Agent` / `LLM` / `Secrets` / `About` 全部改用 `SectionCard`（保存按钮进 footer、28px）；凭据表格套 `TableCard` 表头 |
| 对话页 | 页头标题/副标题改为 `PageHeader` 标准结构，导出/分享改 `RowAction`（28px）；工具卡与审批卡表面统一 `rounded-lg border-zinc-800/60 bg-zinc-900/40`，参数/结果/命令/输出改用 `CodeBlock`，状态改 `StatusPill`，host/凭证/来源改 `Chip`；`ChatInput` 附件与发送按钮统一 28px |
| 边角页 | 首页错误条统一、`PromptCard` 标题 12.5px / 描述 11px；分享页 `PageHeader` 去双内边距；登录页卡片 `rounded-xl` + 统一错误条 |
| 排序与时间 | 分页列表只保留时间列排序（会话 `updated_at`、审批 `created_at`、知识库文档 `updated_at`）；审批新增 `sort_dir`；列表时间统一 `fullDateTime`，不再使用相对时间；会话标题列固定 17rem |
| 测试 | `DesignSystem.test.tsx` 覆盖 RowAction / RowActionIcon / SectionCard 规格断言；通过数不写进文档，以实际运行结果为准 |
| 批次 10–12 | 助理人设与知识范围、技能包创建 / 导入导出 / 工具声明、技能调用 MCP 工具、`ConfirmDialog` / `Toast` 统一弹窗（细则见下方各批） |

#### 批次 0 · 共享原语（后续所有页面的前置）

| 原语 | 位置 | 用途 |
| --- | --- | --- |
| `rowActionClass` | `components/ui/RowAction.tsx` | 把 `RowAction` 的固定尺寸导出为 className，供 `Link`（如「打开会话」）复用，避免再手写 |
| `SectionCard` | `components/ui/SectionCard.tsx` | 设置页 / 详情页分组：标题 12.5px + 说明 11px + 内容 + 可选 footer |
| `DetailStat` | `components/ui/DetailStat.tsx` | 详情页指标格（10px 标签 + 14px 等宽数值），替换 Trace 详情内部实现 |
| `DetailSection` | `components/ui/DetailSection.tsx` | 详情页分组标题（11px 大写 + 可选 danger），统一上/下间距 |
| `CodeBlock` | `components/ui/CodeBlock.tsx` | payload / result / 参数 JSON：`rounded-lg border-zinc-800/60 bg-zinc-950` + `max-h-72` + `text-[11px]` |
| `MetaRow` | `components/ui/MetaRow.tsx` | 等宽键值行 + 复制按钮，Trace 详情与审批详情共用 |

#### 批次 1 · 页头与工具栏（一次改完，消除最高的不一致）

现状问题：同样一个「刷新」，审批/技能是 `h-8 px-2.5`，会话是 `size="sm"`，助理是默认 `h-9`，可观测是 `RowAction h-7`。

| 页面 | 改动 | 文件 |
| --- | --- | --- |
| 审批 | 刷新 → `RowAction`；「返回列表」移入 `PageHeader leading`（仅选中项时出现）；批量批准/拒绝用 `RowAction` | `Approvals.tsx` |
| 会话 | 刷新 → `RowAction`；批量删除保留 `danger size="sm"`；新建会话 `primary size="sm"` | `Sessions.tsx` |
| 技能 | 重载/刷新从 FilterBar 右侧移到 `PageHeader actions`，改用 `RowAction`；FilterBar 只留搜索 + 类别 + 计数 | `Skills.tsx` |
| 助理 | 刷新 → `RowAction`；新建助理 `primary size="sm"` | `Agents.tsx` |
| MCP | 页头「刷新 / 接入 MCP」统一为 `RowAction` + `primary size="sm"` | `Mcp.tsx` |
| 知识库 | 切块设置 / 刷新收成页头右侧按钮组，统一 `h-7`；建/改/上传文档时后端已自动重切块，页面不再提供「重建索引」入口；检索台移到右侧内容区顶部，不再占页头 | `Knowledge.tsx` |
| 工具 / 可观测 | 已符合，仅替换为共享原语 | 无 |

约定：页头次要动作 = `RowAction`（28px）；主操作 = `Button variant="primary" size="sm"`；破坏性 = `danger size="sm"`；同一页头最多 1 个 primary。

#### 批次 2 · 详情页（与 Trace 详情同构）

| 区域 | 改动 |
| --- | --- |
| 审批详情 `ApprovalDetail` | 改为 `PageHeader leading=返回` + 状态胶囊 + `DetailStat` 网格（编号 / 类型 / 提交时间 / 决策时间）+ `DetailSection` + `CodeBlock`；底部操作条 sticky：批准 primary、拒绝 dangerGhost、刷新 RowAction；错误条统一 |
| 工具详情弹窗 | 参数表套 `TableCard` 表头规格（11px / `px-3 py-2`），JSON 用 `CodeBlock` |
| 技能详情弹窗 | 同上；工具列表改用双层行 + chip |
| 助理详情弹窗 | 改 `DetailSection` 分段（简介 / 模型 / 工具 / 技能 / 记忆），计数用 `Chip` |
| 可观测详情 | `DetailMetric` / `SectionTitle` 替换为共享 `DetailStat` / `DetailSection`，视觉不变 |

#### 批次 3 · 列表与卡片页

| 页面 | 改动 |
| --- | --- |
| 技能 | 表头对齐工具页（sticky、`whitespace-nowrap`、描述列 `w-full max-w-0`）；「仅 AI 调用」改为右侧 `Chip tone="info"`；技能分组行折叠图标换 `RowActionIcon` 风格（20px 热区） |
| MCP | `ServerRow` 卡片头部：图标块 + 名称 12.5px + 状态/工具数 `Chip` + 地址等宽 11px；错误条与说明条统一 `rounded-lg border … px-3 py-2` |
| 知识库 | 文档目录改为单容器行列表（标题 + 来源/路径/标签元信息），来源文字与图标统一中性灰，不按来源类型上色；行内动作降到 60% 透明度、悬停/聚焦恢复；行首 16px 把手是唯一拖拽源（整行不设 `draggable`），拖拽预览用标题胶囊；整行不响应点击，只有行内「编辑 / 查看」动作打开文档；目录树选中态用 indigo 底 + 左侧 2px 条，分组头用中性色块图标 + 等宽计数；检索结果按 `01` 起编号，元信息行保留 `向量相似度 / 关键词排名 / RRF`；检索区复用 `FilterBar`，只放默认 `SearchInput`，回车检索，不额外加按钮和范围 footer |
| 助理 | 卡片加来源 `Chip`（内置/自定义）与工具/技能计数；标题 12.5px；详情入口整卡可点，右侧只留编辑/删除/使用 |
| 会话 | 表头与审批一致（sticky；标题列固定 `17rem`，批次 7 起不再自适应吃满整行）；列设置/排序按钮 `RowActionIcon` |
| 工具 | 表格滚动容器与审批一致（sticky thead、`bg-zinc-900/95`） |

#### 批次 4 · 设置

| 区域 | 改动 |
| --- | --- |
| `SettingsLayout` | 左侧 rail 字号 12.5px、图标 14px、active 态 `bg-indigo-500/10` + `ring-1 ring-inset ring-indigo-500/30`；移动端横向滚动保持 |
| `Preferences` | 4 个手写 `<section className="rounded-xl border … p-5">` 全部换 `SectionCard` |
| `Agent` / `LLM` / `Secrets` | 表单分组用 `SectionCard`；标签 `Label` 统一 11px；说明条统一；凭证/模型表格套 `TableCard` 表头规格；保存放 section footer，危险操作靠右 |
| `About` | 外层换 `SectionCard`（logo 进标题、版本/源码/许可证成 11px 键值行），去掉 `dark:` 手写色 |
| 权限不足态 | `SettingsLayout`、`ApprovalDetail` 统一 `EmptyStateCard` |

#### 批次 5 · 对话页（只统一表面，不改排版）

| 区域 | 改动 |
| --- | --- |
| `PageHeader` actions | 分享 / 导出 / 重命名 / 删除收成一组：次要 `RowAction`，危险 `danger size="sm"`，同一行不混 3 种高度 |
| 工具卡 / 审批卡 | 表面统一 `rounded-lg border-zinc-800/60 bg-zinc-900/40`；状态用 `StatusPill`；参数/结果/命令/输出统一 `CodeBlock`；host、凭证、来源条目用 `Chip`；批准 primary / 拒绝 dangerGhost（28px） |
| 思考面板 | 保持纯文本行（阅读节奏优先），仅统一 hover 色 |
| 输入区 | 工具栏图标按钮统一 28px；发送/停止按钮尺寸与页头 primary 对齐 |
| 消息气泡 | 不动排版与字号 |

#### 批次 6 · 边角页

`Home`（错误提示换统一 alert 样式、PromptCard 标题/描述字阶对齐）、`ShareView`（`PageHeader` 去双重内边距、错误条统一）、`Login`（卡片圆角与错误条）、`CommandPalette`、`Sidebar`：只统一颜色 token 与圆角，不改交互密度。

#### 批次 7 · 收尾校准（排序 / 时间格式 / 列宽）

| 区域 | 改动 |
| --- | --- |
| 分页列表排序 | 只开放时间列：会话页仅「更新」（`updated_at`）可排序，标题 / 助理 / 模型列 `enableSorting: false`；后端 `sort_by` 白名单保留，前端只发时间字段 |
| 审批列表 | 「提交时间」表头改为排序开关（`ArrowUp` / `ArrowDown`，`th[aria-sort]`），默认 `desc` 最新在前；后端 `/approvals` 新增 `sort_dir=asc\|desc`，非法值 400；切换排序回到第 1 页 |
| 可观测列表 | 时间列改用 `fullDateTime`（与审批 / 会话一致），去掉「xx 小时前」相对时间 |
| 会话列宽 | 标题列由自适应改为固定 `17rem`，其余列收窄（助理 `8rem`、模型 `9rem`），表格加 `min-w-[60rem]`，窄屏走 `TableCard` 横向滚动，避免标题挤占整行 |
| 详情参数表 | 工具 / 技能详情弹窗参数表套 `og-action-table`，去掉硬编码表头底色 |

#### 批次 8 · 会话管理增强（C1）

| 区域 | 改动 |
| --- | --- |
| 会话列表·标题列 | 标题下加一行元信息（10.5px / `leading-4`）：`N 轮`（用户提问次数，不含工具卡）+ 可选 `M 次工具` / `K 次技能` + 「·」+ 最近一条正文摘要（服务端截断 240 字符），`line-clamp-1` 单行截断；标题仍是唯一可点击的跳转目标，摘要不参与链接文案 |
| 会话列表·模型列 | 模型名下方补 `1.7k tokens`（累计用量，精确值挂 `title`）；没有观测数据时不显示，不写 0 |
| 会话列表·运行列 | 最近一回合状态胶囊：`正常`（ok）/ `进行中`（running，脉冲点）/ `失败`（error，danger）/ `已停止`（stopped，warn）；有待审批时并列 `待审 N` 胶囊，无状态显示 `—` |
| 会话列表·最近活动列 | 显示完整日期时间（`fullDateTime`），按 `last_message_at` 排序（空会话 `NULLS LAST`，不会在倒序时顶到第一页）；不使用「xx 分钟前」相对时间 |
| 会话列表·状态列 | 非置顶/非归档显示 `普通`，避免与运行列的「进行中」混淆 |
| 长会话摘要折叠 | `message_count >= 40` 的会话额外给「展开 / 收起」文字按钮（`aria-expanded`，`aria-label` 带会话标题）；展开后摘要 `whitespace-pre-line` 最多铺满单元格宽度。短会话不给按钮——一行本来就够看 |
| 模型筛选 | FilterBar 新增「模型」胶囊，`FilterPill` + `Select` 与助理筛选同规格；**精确匹配**，选项来自 `GET /chat/sessions/models`（会话里真实出现过的模型），不取模型目录，避免选到查不到结果的值 |
| 对话页·历史折叠 | 消息超过 60 条时只渲染最近 40 条，顶部出现「展开更早的 N 条消息」胶囊按钮；展开后再显示「收起较早消息」。折叠纯展示：消息全量拉取，展开不触发请求，导出/搜索不受影响 |

#### 批次 9 · 引用来源与能力绑定（C3 / A1）

| 区域 | 改动 |
| --- | --- |
| 对话·统一来源区 | 知识与网页检索的来源不再藏在工具卡展开区：`SourcesBlock` 固定渲染在工具卡头部下方，标题 `来源 (N)`，每条 `[序号]` + 标题链接（知识库走 `/knowledge?doc=`，网页走 `https://` 新窗口）+ 路径（mono，10px）+ 块序号（`第 N 块`）+ 相关度（三位小数）+ 摘录（`line-clamp-3`） |
| 对话·气泡内收口 | 一次回答里多次检索时，来源聚合到气泡底部一块（按 `kind + 链接 + 块序号` 去重），工具卡自身不再重复列；历史回放的工具卡各自成行，来源仍固定可见 |
| 对话·引用锚点 | 序号与模型正文里的 `[1]` 对齐；`query_knowledge` 的工具说明要求模型用 `[序号]` 标注引用，避免「有来源但正文无锚点」 |
| 助理编辑·能力绑定 | 「允许使用的工具 / 禁用的工具」合并为一块「能力绑定」：顶部实时预览 `进上下文 N 个工具`（`MCP 可检索 X` + `内置 Y` + `技能 Z` + `随开关 +W`，另显 `已屏蔽 K`），下方按 `内置工具 / 技能 / MCP Server` 三组分列勾选 |
| 助理编辑·按真实单位勾选 | MCP 以 **Server** 为单位挂载（与运行时 `filter_mcp_servers_for_agent` 一致）：Server 行一个复选框，展开看工具清单；未探测成功 / 已停用的 Server 复选框禁用并标黄 |
| 助理编辑·口径不一致时的说明 | 预览下方逐条给提示：白名单不含 MCP 工具（全部 Server 不挂载）、只勾了 Server 内部分工具（未勾选的工具不可见也不可检索）、白名单残留目录里已不存在的工具；「恢复继承全部」一键清空白名单 |
| 助理编辑·屏蔽 | 内置工具 / 技能注入工具 / MCP 工具均给「屏蔽」按钮（写入禁用名单，运行时 `before_tool_callback` 直接拒绝），与「挂载」是两件事，不复用同一个复选框 |

#### 批次 10 · 助理人设与知识范围（A2 / A3）

| 区域 | 改动 |
| --- | --- |
| 助理编辑·模板按钮组 | 三个模板按钮（`SRE 排障` / `知识问答` / `变更助理`）从「只覆盖正文」改为**一次填四块**：系统提示、关键提醒、输出格式、示例。模板值集中在 `web/src/lib/personaTemplates.ts`，页面不再内联文案 |
| 助理编辑·表单分组 | 复合控件（一组模板按钮、示例列表、范围选择）外层用 `FieldGroup`（只渲染 11px 大写分组标题，**不**用 `<label>` 包裹）——`<label>` 会把所有子文本算进第一个按钮的可访问名，读屏与 Testing Library 都会取到一串拼起来的文案 |
| 助理编辑·关键提醒 | 由只读展示改为正文输入框（多行，置于系统提示下方），提示文案说明「会作为 `## 必须遵守` 注入运行时」 |
| 助理编辑·输出格式 | `Select` 三选一（自由 / JSON / 清单）；每个选项下方一句固定说明文案，说明由服务端生成的约束内容，前端不提供自由文本——避免把格式约束写成另一段散文 |
| 助理编辑·示例 | 列表式编辑：每条 `用户输入` + `期望回答` 两个 `Textarea`，右上角「删除」；底部「添加示例」在满 3 条时禁用并提示上限，单条超 2000 字符在字段下方红字提示 |
| 助理编辑·知识范围 | `全部知识库 / 指定范围` 单选；指定时上方「范围目录」多选（`路径` + 文档数）、下方「范围文档」按标题搜索多选。每个复选框带 `aria-label` 前缀（`范围目录 …` / `范围文档 …`），避免目录与文档同名时取不到唯一目标 |
| 助理编辑·命中预览 | 选择变化后调 `scope-preview`，显示「命中 N 篇文档」与各路径命中数；命中 0 篇或路径已不存在时红字阻断（`role="alert"`）并写明「保存会被拒绝」，保存按钮同时禁用 |
| 助理编辑·范围试检索 | 预览下方输入框「按当前范围试检索」，结果只列本范围内命中，每条给路径 + `第 N 块`；未命中显式说「范围内没有命中」，不落回全库 |
| 助理详情·新增三行 | 详情弹窗在「系统提示（原文）」之后补「关键提醒」「知识范围」「输出格式与示例」：范围按路径逐个列出，格式显示中文名，示例显示条数——配了就要看得见 |
| 助理列表·卡片重做 | 卡片从「一个复选框式的摘要」改成信息卡：头像 + 短名 + 来源胶囊 + 行内编辑/删除 → 描述两行定高 → `何时使用` 单行 → 关键提醒（琥珀色左边框块，`line-clamp-2`）→ 四格元信息（工具 / 知识范围 / 输出格式 / 示例，图标 + 字段名 + 值，超长单行省略悬停看全文）→ 底部模型与「使用此助理」 |
| 助理列表·网格 | 卡片信息变多，列数从 `lg:3` 收成 `md:2 / 2xl:3`，单卡宽度约 590px，元信息四格不挤成两行 |
| 助理列表·新建磁贴 | 网格末尾追加虚线「新建自定义助理」磁贴（可写权限且有结果时显示），不再只能靠页头按钮进入；文案刻意不叫「新建助理」，避免与页头按钮的可访问名撞车 |
| 新建/编辑弹窗 | `Modal size="xl"`（`max-w-4xl` 896px）+ `resizable`，原来是默认 `md`（448px）导致人设、示例与能力绑定全部挤在一条窄列；宽屏下名称/描述并排、权限模式/模型/最大轮数三列 |

#### 批次 11 · 技能包（S1–S3 + 助理勾选技能）

| 区域 | 改动 |
| --- | --- |
| 技能列表·页头 | 新增 `新建技能`（primary）与 `导入 ZIP`、`重载`；搜索 + 类别胶囊收进 `FilterBar`，计数用 11px 次要文字 |
| 技能列表·来源区分 | 行内 `来源` 胶囊：`内置`（`backend/skills/` 只读）与 `自定义`（数据库）；只有自定义技能出现编辑 / 删除 |
| 技能列表·导出 | 行内 `导出 ZIP` 走 `RowAction`，下载文件名固定为 `<key>.zip`，导出包可直接再导入（frontmatter 里的 `metadata` 原样保留） |
| 技能详情弹窗 | 分段展示 frontmatter（名称 / 描述 / 许可 / 兼容性 / 声明工具）、`SKILL.md` 正文，以及三类资源清单（`参考资料 references/`、`素材 assets/`、`脚本 scripts/`），每行给路径 + 字节数 + 是否二进制 |
| 新建 / 编辑技能 | `Modal` 表单：key、描述、分类、风险等级、许可 / 兼容性 / allowed-tools、`SKILL.md` 正文、工具声明复选（按内置工具注册表 + 各 MCP Server 探测缓存列出，未知名字直接禁止保存），以及三类资源的路径 + 内容编辑区 |
| 导入 ZIP | 选包后先调 `POST /skills/parse` 出预览（key / 描述 / 资源计数 / 工具），确认后再带 `risk`、`category` 落库；包内已声明时不重复询问，包内缺失时必填并给出错误条 |
| 助理编辑·技能分组 | 能力绑定增加「技能」分组：`全部技能` / `指定技能` 两态，逐个技能勾选挂载；被挂载技能声明的工具显示 `随技能` 并禁用「屏蔽」，取消勾选技能才能去掉 |
| 助理编辑·预览口径 | 顶部预览补 `技能 Z` 与 `随技能 +W`，被锁定的工具不计入 `已屏蔽 K`（与后端 `validate_skill_binding` 同口径） |

#### 批次 12 · 技能调用 MCP 工具

| 区域 | 改动 |
| --- | --- |
| 技能编辑·工具声明 | 「可调用工具」上方加搜索框（内置 + MCP，按名称/说明过滤）；工具按来源分组：`内置工具` 一组、`mcp · <server>` 每个 Server 一组，组头显示该组工具数与已选数，组内 `max-h-44` 滚动 |
| 技能编辑·目录外声明 | 已声明但目录里已没有的工具（Server 删除 / 未探测 / 工具改名）单列「目录外」组，仍可取消勾选，否则旧声明永远删不掉 |
| 技能编辑·不可用提示 | MCP Server 停用或探测失败时，工具行标 `不可用` 胶囊（声明仍可保存，运行时不会挂载） |
| 技能详情·来源标注 | 声明工具胶囊区分来源：MCP 显示 `mcp · <server> · <tool>`（`accent`），目录里没有的显示原 wire 名并转 `warning`；悬停给出 Server 状态说明 |
| 助理编辑·技能声明 MCP | 技能分组里声明的 MCP 工具显示 `mcp` 胶囊（目录外为 `warning`）；预览面板在「进上下文」下方说明「技能声明的 MCP 工具随技能进入检索目录（仍需 search/load 调用）」 |
| 助理编辑·整台挂载口径 | 部分勾选提示把技能声明计入已选数（`勾选 X/Y 个工具（含技能声明）`），并说明未勾选的工具不进检索目录、搜不到也调不了，避免「一个都没勾却显示已挂载」的困惑 |
| 二次确认 | 会话/账号等破坏性操作统一用 `ConfirmDialog`（`Modal` + 取消/危险按钮 + 行内错误），不使用 `window.confirm`；执行中禁用关闭与确认 |
| 知识库·检索设置 | `RowAction` 打开 `Modal size="sm"`，分「切块参数」（`chunk_size` / `chunk_overlap`，字符单位，重叠必须小于大小；改动后保存即自动重建索引）与「检索阈值」（`rrf_min_score`，0 不过滤，保存即生效）两组；保存成功用右下角 `Toast` 提示，切块参数未改动则不重建 |
| 知识库·新建/编辑文档 | `Modal size="xl"`（`max-w-4xl` 896px）+ `resizable`；标题/英文标题、目录/标签两列，来源 URL 与 Markdown 正文通栏 |
| 轻提示 | 成功/失败的操作反馈统一 `Toast`（右下角浮层，5 秒自动消失）；替代 `window.alert`，需要决策的走 `ConfirmDialog` / `Modal` |

#### 每批验收

1. `npx tsc -b --noEmit`、`npx eslint src --max-warnings 0`、`npx vitest run`。
2. 暗色 + 亮色各截一张目标页；页头按钮高度、行内按钮高度用 CDP 量到 28px。
3. `DesignSystem.test.tsx` 增加断言：`RowAction` 固定 `h-7 text-[11px]`、`RowActionIcon` 固定 `h-7 w-7`、`SectionCard` 标题字阶。
4. 守卫 grep：`src/pages` 内不允许出现 `h-8 items-center gap-1.5 px-2.5`、`className="p-1"`、手写 `rounded-xl border border-zinc-800`（管理页的列表 / 详情卡一律走 `TableCard` / `Card`）。登录页的整页品牌卡（`Login.tsx`：60% 表面 + 阴影 + 模糊撑住渐变底）不属于列表卡，不在守卫范围内。

### 不建议强行套用的区域

- 对话正文的消息气泡：阅读节奏优先，不压缩成列表行。
- 侧边栏与命令面板：交互密度优先，只统一颜色与圆角。
- 图表类组件：保持现有可视化语义，不套表格样式。

---

## 6. 单页改造检查清单

- [ ] 页头使用 `PageHeader`，标题/副标题字号符合规范。
- [ ] 搜索与筛选收进一张 `FilterBar`，没有残留 `border-b` 工具条。
- [ ] 搜索框有清空按钮；筛选生效时有高亮和一键清除。
- [ ] 列表使用 `TableCard` 或统一 `Card`，没有页面自写圆角/边框。
- [ ] 表头 11px、单元格 `px-3 py-2`，行内最多两层信息。
- [ ] 状态统一用 `StatusPill`，颜色符合语义表。
- [ ] 页头次要动作是 `RowAction`、主操作是 `primary size="sm"`，同一页头最多 1 个 primary。
- [ ] 行内操作只用 `RowAction` / `RowActionIcon`，没有手写 `h-7 px-2` 或 `p-1` 按钮。
- [ ] 空状态、错误提示使用统一卡片样式，错误文案不溢出。
- [ ] 有分页时使用 `TablePager`，区间文案为 `起-止 / 共 N 条`。
- [ ] 分页列表只开放时间列排序（会话 `updated_at`、审批 `created_at`、知识库文档 `updated_at`），其他列不出现排序箭头。
- [ ] 列表时间统一 `fullDateTime`，不使用「xx 分钟前」相对时间，也不提供相对时间格式化工具。
- [ ] 暗色与亮色主题都验证过对比度。
- [ ] `npx eslint` 与页面测试通过。

---

## 7. 落地约定

- 新页面直接使用本文组件；不再复制 `Observability` 的内部实现。
- 旧页面改造时优先替换骨架（工具栏 + 容器 + 状态），再处理详情弹窗。
- 每次改造只动目标页面，不顺手重构数据层与接口。
- 视觉回归以暗色主题为主、亮色主题为辅；两种主题都要截图确认。

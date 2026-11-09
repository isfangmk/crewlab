# AI 工作台 UI 设计规范（列表与详情改造）

> 版本：v1.0 · 2026-11-09  
> 来源：可观测页（`/observability`）与 Trace 详情改造后形成的视觉语言。  
> 目标：所有管理页使用同一套骨架、字阶、状态语义与分页交互，减少页面间的「换个页面换种风格」。

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

全部从 `@/components/ui` 导入，页面禁止再复制粘贴同类 class。

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
| `Select` / `Input` / `Checkbox` / `Switch` | 表单控件 |

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
| 知识库 `/knowledge` | 检索区改为 FilterBar；命中结果区独立滚动 |

### 已完成（第二阶段：指标卡 + 双层列表）

| 页面 | 改动 |
| --- | --- |
| 审批 `/approvals` | 列表形式（不用户卡片）：`TableCard` + 表头「审批内容 / 状态 / 来源 / 提交时间 / 操作」，行内展开 payload/result，提交时间列附带决策时间；服务端分页 + 提交时间筛选 + 提交时间升/降序 + 关键字搜索；顶部 6 张状态指标卡（可点击过滤，统计随搜索/时间联动） |
| 工具 `/tools` | 顶部 3 张风险指标卡（总数 / 安全 / 需审批）+ FilterBar；表格改为「工具/说明」双层行 + 参数统计列 + 行内运行按钮 |

### 第三阶段：全站对齐计划

原则：先抽共享原语，再按「页头 → 详情 → 列表/卡片 → 设置 → 对话」顺序批量替换；每个批次独立可验收、可回滚，不改数据层。

**进度：批次 0–7 已完成。**

| 已完成项 | 结果 |
| --- | --- |
| 共享原语 | `rowActionClass` / `rowActionIconClass`、`SectionCard`、`DetailSection`、`DetailStat` / `DetailStatGrid`、`CodeBlock`、`MetaRow`，全部从 `@/components/ui` 导出 |
| 页头动作 | 审批 / 会话 / 技能 / 助理 / MCP / 知识库 / 工具统一为 28px；技能页「重载 / 刷新」由 FilterBar 上移到页头；审批「返回列表」移入 `leading` |
| 可观测详情 | 内部 `DetailMetric` / `SectionTitle` / `MetaRow` / `CopyButton` 换成共享原语；「打开会话」链接复用 `rowActionClass` |
| 审批详情 | 改为 `leading` 返回 + 状态胶囊 + 4 格元信息 + 操作内容 / 执行结果 / 决策说明三段 + 底部 sticky 操作条；批准 primary、拒绝 `RowAction danger`、刷新 `RowAction` |
| 详情弹窗 | 工具 / 技能 / 助理详情统一 `DetailSection`；参数表统一 11px 表头 + `px-3 py-2` 单元格，并套 `og-action-table` 让浅色模式表头落到 `rgb(248 250 252)`；系统提示与返回示意改用 `CodeBlock`；助理弹窗底部按钮统一 28px |
| 列表与卡片 | 技能表头与工具页对齐；MCP 服务器卡头部与参数表统一 `Chip` / sticky 表头；知识库 `DocCard` 来源/路径/标签全改 `Chip`，目录计数改 `Chip`，空态动作 28px；助理卡来源与工具/只读改 `Chip`，标题 12.5px |
| 设置页 | `SettingsLayout` rail 12.5px + indigo 选中态、权限不足改 `EmptyStateCard`；`Preferences` / `Agent` / `LLM` / `Secrets` / `About` 全部改用 `SectionCard`（保存按钮进 footer、28px）；凭据表格套 `TableCard` 表头 |
| 对话页 | 页头标题/副标题改为 `PageHeader` 标准结构，导出/分享改 `RowAction`（28px）；工具卡与审批卡表面统一 `rounded-lg border-zinc-800/60 bg-zinc-900/40`，参数/结果/命令/输出改用 `CodeBlock`，状态改 `StatusPill`，host/凭证/来源改 `Chip`；`ChatInput` 附件与发送按钮统一 28px |
| 边角页 | 首页错误条统一、`PromptCard` 标题 12.5px / 描述 11px；分享页 `PageHeader` 去双内边距；登录页卡片 `rounded-xl` + 统一错误条 |
| 排序与时间 | 分页列表只保留时间列排序（会话 `updated_at`、审批 `created_at`）；审批新增 `sort_dir`；可观测时间改 `fullDateTime`；会话标题列固定 17rem |
| 测试 | `DesignSystem.test.tsx` 增加 RowAction / RowActionIcon / SectionCard 规格断言（139 项通过） |

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
| 知识库 | 切块设置 / 重建索引 / 刷新 / 检索收成页头右侧按钮组，统一 `h-7` | `Knowledge.tsx` |
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
| 知识库 | `DocCard` 压成两行（标题 + 路径/来源元信息），右侧 `RowActionIcon`；目录树选中态用 indigo 底 + 左侧 2px 条，计数用 `Chip` |
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
| 可观测列表 | 时间列改用 `fullDateTime`（与审批 / 会话一致），去掉「xx 小时前」相对时间；悬停 `title` 仍是完整时间 |
| 会话列宽 | 标题列由自适应改为固定 `17rem`，其余列收窄（助理 `8rem`、模型 `9rem`），表格加 `min-w-[60rem]`，窄屏走 `TableCard` 横向滚动，避免标题挤占整行 |
| 详情参数表 | 工具 / 技能详情弹窗参数表套 `og-action-table`，去掉硬编码表头底色 |

#### 每批验收

1. `npx tsc -b --noEmit`、`npx eslint src --max-warnings 0`、`npx vitest run`。
2. 暗色 + 亮色各截一张目标页；页头按钮高度、行内按钮高度用 CDP 量到 28px。
3. `DesignSystem.test.tsx` 增加断言：`RowAction` 固定 `h-7 text-[11px]`、`RowActionIcon` 固定 `h-7 w-7`、`SectionCard` 标题字阶。
4. 守卫 grep：`src/pages` 内不允许出现 `h-8 items-center gap-1.5 px-2.5`、`className="p-1"`、手写 `rounded-xl border border-zinc-800`。

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
- [ ] 分页列表只开放时间列排序（会话 `updated_at`、审批 `created_at`），其他列不出现排序箭头。
- [ ] 列表时间统一 `fullDateTime`，不使用「xx 分钟前」相对时间。
- [ ] 暗色与亮色主题都验证过对比度。
- [ ] `npx eslint` 与页面测试通过。

---

## 7. 落地约定

- 新页面直接使用本文组件；不再复制 `Observability` 的内部实现。
- 旧页面改造时优先替换骨架（工具栏 + 容器 + 状态），再处理详情弹窗。
- 每次改造只动目标页面，不顺手重构数据层与接口。
- 视觉回归以暗色主题为主、亮色主题为辅；两种主题都要截图确认。

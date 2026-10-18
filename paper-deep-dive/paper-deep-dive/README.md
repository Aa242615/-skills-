# paper-deep-dive —— 论文深度解析与汇报材料生成 Skill

把一篇论文（PDF / 标题 / DOI / arXiv 编号）变成一整套可直接拿去汇报的材料：

| 产物 | 文件 | 回答什么问题 |
|---|---|---|
| A 论文精读 | `01_论文精读.docx` | 这篇论文到底做了一件什么事、解决了什么问题 |
| B 技术与术语 | `02_技术与术语.docx` | 用了哪些技术、名词是什么意思、论文里怎么用的 |
| C 汇报 PPT | `03_汇报.pptx` | 封面为英文原题 + 作者 + 会议/刊；**版式/字体/格式与 `references/group-meeting-sample.pptx` 一致；每一页（含封面）带中南大学校徽**；排版经三轮检查 |
| D 相关工作与引用脉络 | `04_相关工作.docx` | 相关论文分别说了什么、本文引用了谁、后续谁用了本文 |
| E 可延伸的 Idea | `05_idea.docx`（或逐条 `.docx`） | 在当前技术条件下，这篇论文还能往哪走 |

---

## 一、安装（二选一）

### 方式 1：装成 Claude Code 的 skill（推荐，之后直接说"分析这篇论文"就行）

把整个 `paper-deep-dive` 文件夹复制到用户的 skills 目录：

```
# Windows
C:\Users\<用户名>\.claude\skills\paper-deep-dive\

# macOS / Linux
~/.claude/skills/paper-deep-dive/
```

复制完重启 Claude Code，`/skills` 里能看到 `paper-deep-dive` 即安装成功。

### 方式 2：不装，直接把内容喂给任意 AI

打开 `references/prompt-template.md`，整段复制粘贴到 claude.ai / ChatGPT / 其它支持长文本的模型里，效果接近。适合在别人的电脑上临时用。

---

## 二、怎么用

装好后，在 Claude Code 里这样开口就行：

```
用 paper-deep-dive 分析这篇论文：D:\papers\transformer.pdf
汇报场合：组会文献汇报，20 分钟，听众是同方向研究生
输出到 D:\report\
```

```
分析 arXiv:1706.03762，做成第一次汇报的材料
```

也可以只说「帮我把这篇论文做成 PPT」——skill 会先精读、先核对，再动手做 deck。**这个顺序是刻意的**：论文汇报最大的失败模式不是做得不够多，而是把论文读错了然后做了 20 页 PPT。

### 关键约定

- **说完「第一次汇报」之后，会被追问几个问题**（时长、听众、页数上限、语言、输出目录，以及 idea 要不要每条单独一份文档）。一次问全，不会来回挤牙膏。
- **读完论文后会先给一页纸大纲**（一句话概括 + PPT 目录 + 五份产物的文件名），确认后才生成正式产物。误解在这一步被发现，改三行字；在 PPT 做完后发现，改全部。
- **每完成一份产物就说一声**，不会五份一起丢过来。
- 默认使用中文输出；论文标题、作者、模型名、数据集名、公式、DOI 和代码命令保留原文。
- A、B、D、E 以详细 Word 文档交付。Markdown 只作为内部中间稿，不作为默认最终文件。
- PPT 使用白底和较大字号，采用左文右图、左图右文、上文下图或上图下文的清楚分区；使用原生项目符号和缩进，避免数字列表和大色块。

---

## 三、目录结构

```
paper-deep-dive/
├── SKILL.md                      主流程：五步走完整个汇报材料
├── README.md                     本文件
├── references/
│   ├── analysis-templates.md     产物 A / B / E 的逐项模板与好坏示例
│   ├── ppt-layout.md             PPT 结构、排版数值、配图规范、三轮检查
│   ├── group-meeting-template.md 论文汇报 PPT 版式模板（所有场合默认，源自样例）
│   ├── ppt-reference-notes.md     主样例与补充样板的版式吸收说明
│   ├── group-meeting-sample.pptx 版式样例（风格/字体/格式的最终依据）
│   ├── _sample_spec.txt          样例全量规格 dump（坐标/字号/字体）
│   ├── assets/
│   │   └── csu-logo.png          中南大学校徽（每一页必放，含封面）
│   ├── literature-search.md      引用脉络怎么做、用哪些源、防幻觉规则
│   ├── idea-docx.md              每个 idea 单独出一份 Word / 一页 PPT 的规范
│   └── prompt-template.md        纯 prompt 版，用于无 skill 的环境
└── scripts/
    ├── extract_pdf.py            抽正文 / 抽内嵌原图 / 按 dpi 渲染整页
    ├── search_papers.py          多源文献检索与引用核验（OpenAlex / Crossref / S2）
    ├── check_deck.py             交付前自动检查封面要素、字号、图片清晰度、
    │                             备注、元素重叠、每页校徽、图片越界
    └── _dump_sample_spec.py      重新提取样例 PPT 的全量版式规格
```

---

## 四、脚本用法

依赖：`pip install pymupdf python-pptx requests`（三个都装）。

```bash
# 从 PDF 取素材
python scripts/extract_pdf.py info    paper.pdf
python scripts/extract_pdf.py text    paper.pdf -o paper.txt      # 带页码分隔的正文
python scripts/extract_pdf.py figures paper.pdf -o figs/          # 抽内嵌原图（比截图清楚）
python scripts/extract_pdf.py pages   paper.pdf -o pages/ --dpi 300 --pages 3-7

# 引用脉络
python scripts/search_papers.py resolve "1706.03762"              # 三源交叉解析元信息
python scripts/search_papers.py refs    "1706.03762" --limit 15   # 本文引用了谁
python scripts/search_papers.py cites   "1706.03762" --limit 15 --influential
python scripts/search_papers.py verify  "10.1109/icassp39728..." "1706.03762"
python scripts/search_papers.py related "Swin Transformer"

# 交付前检查 PPT
python scripts/check_deck.py 03_汇报.pptx --report check.md
```

`check_deck.py` 退出码 0 表示没有 ERROR，可以直接拿来当交付卡口。它自动检查：封面要素、字号下限、东亚字体、图片分辨率、讲稿备注、文本溢出、元素重叠、**每页右上角校徽**、图片是否越出页面。**脚本查不出视觉上的拥挤和错位，产出后仍要打开 PPT 完整目视检查两遍（整体一遍 + 逐页对照清单一遍）。**

---

## 五、关于 Undermind

Undermind 是付费的深度文献检索服务，**没有开放 API**，脚本无法调用。skill 里对它的处理方式是：

1. 把整理好的检索式交给用户，请用户自己跑一遍，把结果（标题 + 链接 + 摘要）贴回来，由 Claude 整理排版；
2. 同时用 OpenAlex / Crossref / Semantic Scholar 并行检索一轮，作为交叉验证；
3. **不把"用了 Undermind"当成严谨性的证明**——严谨性来自每条引用都能被点开核验。

---

## 六、五条硬性规则（决定了产物能不能拿出去）

1. **不编造文献。** 每条引用必须有 DOI / arXiv ID / 官方页面 URL，且实际访问核对过。查不到的宁可不写；只检索到 2 条就写 2 条并注明"文献较新"。用户宁可要 2 条真的，也不要 10 条编的。
2. **事实与解读分开。** 论文原文写的是一回事，你的理解是另一回事。推测处一律标注「推断」——导师会追问"这是作者说的还是你说的"。
3. **数值必须有出处。** 每个指标、每个结论都标注章节号 / 图号 / 表号 / 公式号，让用户被追问时能立刻翻到原文那一页。
4. **术语写成「英文（中文）」。** 中文译名极不统一，保留英文原词才能让用户自己去检索。
5. **封面必有四项。** 英文原题 + 全部作者 + 会议/期刊全称 + 年份。用户会明确点名，也最容易漏。

## 七、PPT 的硬性约定（产物 C 专属）

1. **主风格遵循原样例，补充样板只提供排版经验。** `03_汇报.pptx` 以 `references/group-meeting-sample.pptx` 的画布、标题、校徽和整体气质为主，同时采用更大的正文和更清楚的段落缩进：标题约 30pt，正文 22–24pt，图注 16–18pt。
2. **每一页都有中南大学校徽（含封面）。** 素材 `references/assets/csu-logo.png`，统一放右上角 L=10.42" T=0.18" W=2.38" H=0.66"，不被任何元素遮挡。
3. **白底、图文分区、无大色块。** 每页优先采用四种分区布局之一，图片和文字大致各占一半；不使用“文字+下面色块”的版式，不让文字压在彩色背景上。
4. **配图必须完整 + 交付前三轮检查。** 论文图片整张呈现（含图注/坐标轴/图例），禁止截半张；产出后依次跑脚本检查、整体目视、逐页对照清单，任何一轮发现问题改完重跑三轮。

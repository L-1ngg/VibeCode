# VibeCode

VibeCode 相关资源集合，包含 MCP 配置、Skills 技能、提示词等内容。

## 目录结构

```
.
├── MCP/        # MCP (Model Context Protocol) 相关配置与资源
├── SKILLS/     # Skills 技能定义与模板
└── PROMPT/     # 常用 PROMPT 收集与整理
```

## Git Commit 规范

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 规范。

### 提交格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

- `type`（必填）：提交类型
- `scope`（可选）：影响范围
- `subject`（必填）：简短描述，不超过 50 个字符，不以句号结尾
- `body`（可选）：详细描述，说明修改的动机与前后对比
- `footer`（可选）：关联 Issue 或标注 Breaking Change

### Type 类型说明

| 类型       | 说明                                         |
| ---------- | -------------------------------------------- |
| `feat`     | 新功能                                       |
| `fix`      | 修复 Bug                                     |
| `docs`     | 文档变更                                     |
| `style`    | 代码格式（不影响逻辑的变动，如空格、分号等） |
| `refactor` | 重构（既不是新功能，也不是修复 Bug）         |
| `perf`     | 性能优化                                     |
| `test`     | 添加或修改测试                               |
| `chore`    | 构建过程或辅助工具的变动                     |
| `ci`       | CI/CD 配置变更                               |
| `revert`   | 回滚提交                                     |

### 示例

```bash
# 新功能
feat(mcp): add context7 MCP tool configuration

# Bug 修复
fix(skills): correct template variable parsing error

# 文档更新
docs: update README with commit convention

# 重构
refactor(prompt): simplify prompt loading logic

# 包含 body 和 footer 的完整示例
feat(mcp): add new mcp tool

Add support for context7 MCP server integration,
enabling real-time documentation retrieval.

Closes #12
```

### Breaking Change

当提交包含不兼容变更时，在 `type` 后加 `!` 或在 `footer` 中注明：

```bash
feat(mcp)!: redesign MCP configuration format

BREAKING CHANGE: MCP config files now use YAML instead of JSON.
```

## 贡献

欢迎提交 Issue 和 Pull Request。

## License

MIT

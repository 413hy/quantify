# Trading framework diagram

`trading-framework.drawio` is the editable architecture source for the current strategy-free
framework. It was prepared for use with
[Next AI Draw.io](https://github.com/DayuanJiang/next-ai-draw-io), whose MCP server can load, edit,
preview and export draw.io XML.

The three pages intentionally separate:

1. capabilities that exist in the repository now;
2. the decision layer a future project must implement;
3. the current Debian runtime and production lock boundary.

## Direct previews

These PNG/SVG pairs can be opened directly by a browser and are rendered by GitHub without loading
an editor:

- `preview/01-current-framework.png` / `preview/01-current-framework.svg`
- `preview/02-new-project-flow.png` / `preview/02-new-project-flow.svg`
- `preview/03-debian-runtime.png` / `preview/03-debian-runtime.svg`

The image files are view-only previews. `trading-framework.drawio` remains the editable source of
truth and contains all three pages.

The diagram is descriptive, not an authorization artifact. In particular, yellow dashed nodes on
page 2 are future work and must not be interpreted as implemented strategy code.

## Open and edit with Next AI Draw.io

Configure its MCP server in a compatible client:

```json
{
  "mcpServers": {
    "drawio": {
      "command": "npx",
      "args": ["@next-ai-drawio/mcp-server@latest"]
    }
  }
}
```

Then start a session and load the absolute file path:

```text
/root/quantify/ai-quant-system/docs/architecture/trading-framework.drawio
```

A useful editing prompt is:

```text
加载这份三页 draw.io 架构图。保持“现有能力、未来扩展、生产锁定”三种状态不混淆，
不要把旧 V4/V5 策略 campaign 画回来。保留通用自动交易引擎，并明确它只消费未来项目
提供的完整交易意图。优化排版和连线，但保留 NO_BUILTIN_STRATEGY、
UNATTENDED_DISABLED 与 PRODUCTION_RISK_LOCKED 边界。
```

The application can export the loaded diagram as `.drawio`, `.svg` or `.png`. Do not place runtime
credentials, account identifiers or raw evidence in the diagram.

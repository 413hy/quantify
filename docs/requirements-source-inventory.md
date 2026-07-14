# Requirements source inventory

Recorded at: `2026-07-14T06:30:00Z`  
Implementation repository: `/root/quantify/ai-quant-system`  
Immutable source parent: `/root/quantify/reference-materials`

## Authority order

1. The user's explicit requirements in the current implementation task.
2. The immutable `vps.7z` development documents, contracts, configuration schemas, and runbooks.
3. Current official Binance and OpenAI primary documentation as of the implementation date.
4. Louie Price Action material, methodology only.
5. PYTA Order Flow material, methodology only.
6. Other open-source projects, third-party writing, or engineering inference.

Louie/PYTA market parameters are not production defaults and cannot override risk, execution, data, security, or deployment rules. No third-party project has been adopted as an implementation dependency.

## Outer archives and extraction roots

| Archive | Expected and observed SHA-256 | Result | Read-only extraction root |
|---|---|---|---|
| `vps.7z` | `33d2a3f1bd5239c0d3d608b65ab1c0ffc785c0c295dd07846f60dabe153bbf99` | exact | `/root/quantify/reference-materials/vps-archive/vps` |
| `louie_price_action_system_v1.zip` | `8a7dfd6a41191f4d7c290b4dd626175344b041ae33c89da60c9f63f1bc571628` | exact | `/root/quantify/reference-materials/louie-archive/louie_price_action_system_v1` |
| `PYTA_OrderFlow_Quant_System_Spec_v0.1.0.zip` | `e3d2622cd2ca30558399a7ab53a2d11803f64b95e9a4080f4a0f8425eb2d8b3e` | exact | `/root/quantify/reference-materials/pyta-archive/PYTA_OrderFlow_Quant_System_Spec` |

`DOCUMENT_ROOT` is the directory containing `VPS_CODEX_START_HERE.md`: `/root/quantify/reference-materials/vps-archive/vps`.

## Complete machine-verifiable inventory

The following checked-in evidence files are the complete per-file inventories, including SHA-256 and relative path. They are normative for this audit rather than a manually abbreviated list:

- `evidence/preflight/2026-07-14/development/vps-source-inventory.sha256`: 144 physical files, including `MANIFEST.sha256`; the internal manifest itself lists and validates 143 payload files.
- `evidence/preflight/2026-07-14/development/louie-source-inventory.sha256`: 25 physical files, including `MANIFEST.sha256`; the internal manifest lists and validates 24 payload files.
- `evidence/preflight/2026-07-14/development/pyta-source-inventory.sha256`: 16 physical files; `MANIFEST.txt` lists all 16 paths and the extracted inventory is exact.

No source file is writable and no symlink exists under the immutable source parent. The source trees were not used as development directories.

## Material read in full

The audit covered every file required by the task, not only summaries:

- root `README.md`, `VPS_CODEX_START_HERE.md`, `DOCUMENT_ACCEPTANCE_REPORT.md`, and `MANIFEST.sha256`;
- every document in `docs/01` through `docs/14` and both files under `docs/superpowers/specs/`;
- all files under `contracts/`, `config/`, `runbooks/`, and `diagrams/`;
- all Louie Markdown, YAML/JSON configuration, JSON Schema, prompts, pseudocode, test vectors, source/limit notices, and its manifest;
- all PYTA Markdown, YAML/JSON configuration, JSON Schema, prompts, source/limit notices, manifest, and the complete DOCX XML text.

## Validation results

| Check | Result |
|---|---|
| Outer archive SHA-256 | PASS, 3/3 exact |
| Unsafe archive members / symlinks | PASS, none found |
| VPS internal manifest | PASS, 143/143 |
| Louie internal manifest | PASS, 24/24 |
| PYTA inventory | PASS, 16/16 |
| JSON/YAML parsing and duplicate keys | PASS, 86 JSON and 11 YAML in baseline |
| JSON Schema and examples | PASS, 42 schemas; 39 contract and 14 config instances |
| JCS hash examples | PASS, 26 |
| OpenAPI 3.1 parsing/validation | PASS |
| Draw.io/SVG XML | PASS |
| Internal Markdown links | FAIL, two low-risk broken anchors in immutable runbooks |

The broken links are `runbooks/07_DISK_ARCHIVE_INCIDENT.md` and `runbooks/09_UPGRADE_ROLLBACK.md` targeting `00_HOST_RATE_CONTROL.md#5-故障语义`; the actual heading is section 8. The source package remains unmodified. See `document-package-audit.json` for exact results.

## Dynamic official sources checked

Access date: `2026-07-14` UTC.

- Binance USDⓈ-M [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info), [WebSocket streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect), [routing migration](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice), [user data streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams), [error codes](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code), [local order book](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly), and [change log](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log).
- OpenAI [model guidance](https://developers.openai.com/api/docs/models) and [full model list](https://developers.openai.com/api/docs/models/all), plus the current authenticated Codex account catalog produced by `codex-cli 0.144.4`.

Official facts still aligned with the baseline include production `/public`, `/market`, `/private` routing, 24-hour WebSocket lifetime, ping every 3 minutes, 10-minute pong timeout, 10 inbound messages/s, 1,024 streams/connection, `U/u/pu` reconstruction, 503 exact-message classes, `-1008` reduce-only/close-position exemption, Algo Service endpoints, `ALGO_UPDATE`, RPI exclusion from standard books, and the `nq` normal-quantity field.

Two material differences are recorded in ADR 0001 and block only their affected stages: the current Binance Testnet WebSocket base differs from the frozen schema, and the exact `gpt-5.6` slug is absent from the current account Codex catalog even though OpenAI API documentation describes it as an alias for `gpt-5.6-sol`.


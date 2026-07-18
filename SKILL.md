---
name: ma_skill_zotero-to-notebooklm
description: Automates exporting Zotero collections and uploading their PDF attachments to a NotebookLM notebook.
---

# Zotero to NotebookLM Integration

This skill automates the transfer of PDF attachments from a specified Zotero collection to a Google NotebookLM notebook using the Zotero Web API, Model Context Protocol (MCP) and `notebooklm-mcp-cli` tools, while generating and maintaining a JSON mapping file for catalog matching.

This skill does NOT support synchronization of items *from* NotebookLM *to* Zotero. Sources should be added to Zotero first, then uploaded to NotebookLM using this skill.

## Prerequisites

1. **Zotero Web API Credentials**: Setup a `.env` file in the root of your workspace containing:
   - `ZOTERO_LIBRARY_ID`: Your Zotero user ID or group ID (e.g. `6611671`).
   - `ZOTERO_LIBRARY_TYPE`: Either `group` or `user`.
   - `ZOTERO_API_KEY`: Your Zotero API Write-Key.
2. **NotebookLM CLI**: The `notebooklm-mcp-cli` package must be installed and authenticated.
   * Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm doctor` to check status.
   * Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm login` if authentication is missing or expired.
3. **Python Virtual Environment & pyzotero**:
   * Create a virtual environment and install dependencies:
     ```bash
     python -m venv .venv
     .venv\Scripts\pip install pyzotero pypdf
     ```

## Usage

### 0. Creating a NotebookLM Notebook (Manual & Automatic)

- **Automatic Creation**:
  When synchronizing a collection, the `sync_collection.py` script automatically checks if a Google NotebookLM notebook with the same name exists. If it is not found, it automatically creates it using the CLI and retrieves its ID before proceeding with downloads and uploads.

- **Manual Creation via CLI**:
  To manually create a new NotebookLM notebook:
  ```bash
  uvx --link-mode=copy --from notebooklm-mcp-cli nlm notebook create "<NOTEBOOK_NAME>"
  ```

### 1. Synchronizing a Zotero Collection (Initial & Incremental)

You can sync items from your Zotero collections to Google NotebookLM by running the sync script:

- **Sync a specific collection by name**:
  ```bash
  python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/sync_collection.py --collection-name "<COLLECTION_NAME>"
  ```
- **Sync all registered collections**:
  ```bash
  python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/sync_collection.py --all
  ```
- **List synchronized collections**:
  ```bash
  python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/sync_collection.py --list
  ```

#### Intent Triggering
The agent should map the following user intents to this script:
* When user asks to sync or upload a Zotero collection (e.g. `"sync Restorative cataloguing"`), run the sync script with `--collection-name "Restorative cataloguing"`.
* When user asks `"sync zotero to NotebookLM"`, run the sync script with `--all`.
* When user asks to see what collections are synchronized, run the sync script with `--list`.

### 2. Relinking Lost Item Connections

If the `zotero_uploaded_items.json` file is deleted or lost, you can reconstruct it from the existing sources in the NotebookLM notebook and Zotero using the Web API:

```bash
python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/relink_items.py \
  --notebook-name "<NOTEBOOK_NAME>"
```

#### Arguments
* `--notebook-name`: The title of the notebook in NotebookLM.

### 3. Updating Zotero Citation Keys and NotebookLM Source Names

To calculate citation keys, retrieve missing publication dates, write them back to Zotero (via the Zotero Web API), and rename matching NotebookLM sources:

```bash
python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/update_citation_keys.py \
  --collection-id <COLLECTION_KEY> \
  --notebook-name "<NOTEBOOK_NAME>"
```

#### Arguments
* `--collection-id`: The alphanumeric or integer ID of the Zotero collection to update (required).
* `--notebook-name`: The title of the notebook in NotebookLM containing the sources to rename (required).
* `--dry-run`: (Optional) Perform calculations and print updates without making changes.
* `--revert`: (Optional) Roll back the most recent batch of updates applied to Zotero (by loading Zotero data from the local `zotero_changes.json` changelog).


## How it Works

1. The integration connects to Zotero via the HTTP Web API (using `pyzotero` with credentials loaded from `.env`).
2. The `sync_collection.py` script queries all items in a specified Zotero collection via the Web API.
3. For items not yet present in `zotero_uploaded_items.json`, it queries child attachments via Zotero Web API.
4. For each PDF attachment found, the script downloads the file locally using Zotero's attachment dump API, uploads it to NotebookLM using `nlm source add` with a standardized title format (`{author}-{year}-{zotero_key}`), assigns it to a collection label, and immediately deletes the temporary local download.
5. Upon successful uploads, the script saves or merges the metadata into `zotero_uploaded_items.json` in the workspace root directory.
6. The `relink_items.py` script queries the NotebookLM notebook, extracts Zotero keys from formatted source titles, fetches the items from the Zotero Web API, and reconstructs the `zotero_uploaded_items.json` matching database.
7. The `update_citation_keys.py` script calculates citation keys, crawls page metadata/sitemaps for missing publication years, writes date and citationKey updates directly to the Zotero library using the Zotero Web API (with credentials loaded from `.env`), renames matched NotebookLM sources, and subsequently runs `relink_items.py` to keep the JSON registry synchronized.
8. A changelog of all Zotero updates is stored in `zotero_changes.json` to allow full reversal of applied changes when using `--revert`.


## Converting NotebookLM Deep Research Reports to Pandoc Markdown

When converting a "Deep Research Report" generated by NotebookLM to Pandoc-compliant Markdown:

1. **Mapping Numbered Citations**:
   - Extract the mapping from the numbered references list at the end of the report to the corresponding Zotero keys.
   - Replace in-text numeric citations `[X]` with Pandoc-style citation keys `[@citationKey]`.
   - Separate multiple citations with semicolons (e.g., `[15, 16]` -> `[@MAS-2026-ZPTC7SX4; @MAS-2023-ZJ4E8MU8]`).

2. **Positioning Punctuation**:
   - In Pandoc markdown, citations must be positioned *before* punctuation marks.
   - Use a regular expression pattern like `([.,;?!])(\[@.*?\])` to identify punctuation followed by citation keys, and swap them using ` \2\1` (adding a leading space before the opening bracket and moving the punctuation mark immediately after the closing bracket).
     * *Example*: `preservation.[@MAS-2026-QC9L85AV]` becomes `preservation [@MAS-2026-QC9L85AV].`

3. **Cleaning References Section**:
   - Maintain the `### References` (or equivalent) header, but remove the manual list of numbered reference definitions underneath. Pandoc dynamically compiles and appends the bibliography list at the location of this header when generating the document.

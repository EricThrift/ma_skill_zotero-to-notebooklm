---
name: ma_skill_zotero-to-notebooklm
description: Automates exporting Zotero collections and uploading their PDF attachments to a NotebookLM notebook.
---

# Zotero to NotebookLM Integration

This skill automates the transfer of PDF attachments from a specified Zotero collection to a Google NotebookLM notebook using the Model Context Protocol (MCP) and `notebooklm-mcp-cli` tools, while generating and maintaining a JSON mapping file for catalog matching.

## Prerequisites

1. **Zotero Desktop**: Installed and running locally.
2. **NotebookLM CLI**: The `notebooklm-mcp-cli` package must be installed and authenticated.
   * Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm doctor` to check status.
   * Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm login` if authentication is missing or expired.

## Usage

### 1. Uploading a Zotero Collection

Run the Python script provided in the skill directory to copy, upload PDF files, and write the metadata mapping file (`zotero_uploaded_items.json`):

```bash
python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/upload_collection.py \
  --collection-id <ZOTERO_COLLECTION_ID> \
  --notebook-name "<NOTEBOOK_NAME>"
```

#### Arguments

* `--collection-id`: The integer ID of the Zotero collection (e.g., `858`). You can find this by querying the `collections` table in `zotero.sqlite`.
* `--notebook-name`: The title of the notebook in NotebookLM (e.g., `"Museum Catalogues"`). The script will reuse the notebook if it already exists or create a new one.
* `--zotero-db`: (Optional) Custom path to the `zotero.sqlite` database file.
* `--zotero-storage`: (Optional) Custom path to the Zotero `storage/` directory containing PDF attachments.

### 2. Relinking Lost Item Connections

If the `zotero_uploaded_items.json` file is deleted or lost, you can reconstruct it from the existing sources in the NotebookLM notebook and your local Zotero database:

```bash
python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/relink_items.py \
  --notebook-name "<NOTEBOOK_NAME>"
```

#### Arguments

* `--notebook-name`: The title of the notebook in NotebookLM.
* `--zotero-db`: (Optional) Custom path to the `zotero.sqlite` database file.

### 3. Synchronizing Zotero to NotebookLM

You can synchronize items that have been newly added to your Zotero collections by running the sync script:

- **Sync all registered collections**:
  ```bash
  python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/sync_collection.py --all
  ```
- **Sync a specific collection by name**:
  ```bash
  python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/sync_collection.py --collection-name "<COLLECTION_NAME>"
  ```
- **List synchronized collections**:
  ```bash
  python .agents/skills/ma_skill_zotero-to-notebooklm/scripts/sync_collection.py --list
  ```

#### Intent Triggering
The agent should map the following user intents to these scripts:
* When user asks `"sync zotero to NotebookLM"`, run the sync script with `--all`.
* When user asks `"sync {zotero collection name}"` (e.g., `"sync catalogues"`), run the sync script with `--collection-name "{zotero collection name}"`.
* When user asks to see what collections are synchronized, run the sync script with `--list`.

## How it Works

1. The `upload_collection.py` script copies the `zotero.sqlite` database file to a temporary file to avoid locking issues.
2. It queries `collectionItems` and `itemAttachments` to extract parent metadata (authors, dates, keys, titles, itemtypes) and local PDF file paths.
3. For each located PDF, it uploads the file to NotebookLM using `nlm source add` with a standardized target title format: `{author}-{year}-{zotero_key}`.
4. Upon successful uploads, it creates or merges the metadata into `zotero_uploaded_items.json` in the current working directory, using the Zotero API identifiers `groupID` and `collection_key` and tags each source in NotebookLM using the Zotero collection name.
5. The `relink_items.py` script queries the NotebookLM notebook, extracts Zotero keys from the `{author}-{year}-{zotero_key}` formatted titles, matches them back against the Zotero database, and generates the `zotero_uploaded_items.json` matching list.
6. The `sync_collection.py` script compares local Zotero collection items against `zotero_uploaded_items.json` records, uploads any untransferred items, automatically tags/groups them under the collection name, and updates the JSON registry.
7. All temporary database copies are deleted upon completion.

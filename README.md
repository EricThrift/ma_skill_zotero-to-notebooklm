# Zotero to NotebookLM Integration Skill (`ma_skill_zotero-to-notebooklm`)

This skill automates the transfer of PDF attachments from a Zotero collection to Google NotebookLM, utilizing `notebooklm-mcp-cli` tools. It tracks uploaded sources via a `zotero_uploaded_items.json` catalog map to enable synchronization and avoid duplicate uploads.

## Features
- **Collection Upload**: Queries Zotero's Web API, retrieves PDF attachment files for a collection, and uploads them to a NotebookLM notebook.
- **Incremental Sync**: Synchronizes newly added items in Zotero collections to Google NotebookLM using Zotero's Web API.
- **Relink Registry**: Reconstructs the local JSON mapping database (`zotero_uploaded_items.json`) from the existing sources inside NotebookLM and Zotero.

## Structure
```
ma_skill_zotero-to-notebooklm/
├── SKILL.md             # Agent instruction file
├── README.md            # This documentation file
└── scripts/
    ├── sync_collection.py   # Web API sync script
    ├── relink_items.py      # Reconstruction script
    └── update_citation_keys.py # Citation key generation & NotebookLM renaming script
```

## Setup & Prerequisites
1. **Zotero Web API Credentials**: Setup a `.env` file in your workspace root containing:
   ```env
   ZOTERO_LIBRARY_ID=<YOUR_LIBRARY_ID>
   ZOTERO_LIBRARY_TYPE=group  # or user
   ZOTERO_API_KEY=<YOUR_ZOTERO_API_WRITE_KEY>
   ```
2. **NotebookLM CLI**: The `notebooklm-mcp-cli` tool package must be installed and authenticated.
   - Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm doctor` to verify setup.
   - Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm login` to authenticate.
3. **Python Dependencies**:
   - `pyzotero`

## Usage
The agent invokes the scripts automatically depending on the user request. They can also be executed manually:
- **Sync All Registered Collections**:
  ```bash
  python scripts/sync_collection.py --all
  ```
- **Sync Specific Collection**:
  ```bash
  python scripts/sync_collection.py --collection-name "<COLLECTION_NAME>"
  ```
- **Relink Mapping Registry**:
  ```bash
  python scripts/relink_items.py --notebook-name "<NOTEBOOK_NAME>"
  ```
- **Update Citation Keys & Rename Sources**:
  ```bash
  python scripts/update_citation_keys.py --collection-id <COLLECTION_KEY> --notebook-name "<NOTEBOOK_NAME>"
  ```

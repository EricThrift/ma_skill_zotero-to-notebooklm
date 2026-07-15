# Zotero to NotebookLM Integration Skill (`ma_skill_zotero-to-notebooklm`)

This skill automates the transfer of PDF attachments from a local Zotero collection to Google NotebookLM, utilizing `notebooklm-mcp-cli` tools. It tracks uploaded sources via a `zotero_uploaded_items.json` catalog map to enable synchronization and avoid duplicate uploads.

## Features
- **Collection Upload**: Reads Zotero's local SQLite database, retrieves PDF attachment files for a collection, and uploads them to a NotebookLM notebook.
- **Incremental Sync**: Synchronizes newly added items in local Zotero collections to Google NotebookLM.
- **Relink Registry**: Reconstructs the local JSON mapping database (`zotero_uploaded_items.json`) from the existing sources inside NotebookLM and Zotero.

## Structure
```
ma_skill_zotero-to-notebooklm/
├── SKILL.md             # Agent instruction file
├── README.md            # This documentation file
└── scripts/
    ├── upload_collection.py # Initial collection upload script
    ├── sync_collection.py   # Incremental sync script
    └── relink_items.py      # Reconstruction script
```

## Setup & Prerequisites
1. **Zotero Desktop**: Installed and running locally.
2. **NotebookLM CLI**: The `notebooklm-mcp-cli` tool package must be installed and authenticated.
   - Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm doctor` to verify setup.
   - Run `uvx --link-mode=copy --from notebooklm-mcp-cli nlm login` to authenticate.

## Usage
The agent invokes the scripts automatically depending on the user request. They can also be executed manually:
- **Upload Collection**:
  ```bash
  python scripts/upload_collection.py --collection-id <ZOTERO_COLLECTION_ID> --notebook-name "<NOTEBOOK_NAME>"
  ```
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
